import asyncio
import io
import json

import httpx
from PIL import Image

from radar import collect as C
from radar import detect, sources, trends
from radar.db import connect


def run(coro):
    return asyncio.run(coro)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_parse_domains_filters_garbage():
    text = "Example.com\n*.shop.com.tr\nnot a domain\n\nexample.com\nbad_.com\n"
    assert sources.parse_domains(text) == ["example.com", "shop.com.tr"]


def test_prioritize_puts_tr_first():
    assert sources.prioritize(["a.com", "b.com.tr", "c.net"]) == ["b.com.tr", "a.com", "c.net"]


def test_whoisds_url_token():
    import datetime as dt
    url = sources.whoisds_url(dt.date(2026, 10, 19))
    assert url.endswith("/nrd") and "MjAyNi0xMC0xOS56aXA=" in url


def test_verify_shopify():
    def handler(req):
        if req.url.host == "shop.example":
            return httpx.Response(200, json={"products": []})
        if req.url.host == "html.example":
            return httpx.Response(200, text="<html>")
        return httpx.Response(404)

    async def go():
        async with client_for(handler) as c:
            return (await detect.verify_shopify(c, "shop.example"),
                    await detect.verify_shopify(c, "html.example"),
                    await detect.verify_shopify(c, "none.example"))
    assert run(go()) == (True, False, False)


def test_dns_looks_shopify():
    class Rd:
        def __init__(self, a): self.address = a

    class Ans(list):
        canonical_name = "x.example."

    class FakeResolver:
        async def resolve(self, name, kind):
            if name == "shopify.example":
                return Ans([Rd("23.227.38.65")])
            raise OSError("nxdomain")
    assert run(detect.dns_looks_shopify("shopify.example", FakeResolver())) is True
    assert run(detect.dns_looks_shopify("other.example", FakeResolver())) is False


def test_currency_and_products_pagination():
    page2 = [{"id": 1000 + i} for i in range(10)]

    def handler(req):
        if req.url.path == "/":
            return httpx.Response(200, text='<script>Shopify.currency = {"active":"TRY","rate":"1.0"};</script>')
        if req.url.path == "/products.json":
            if req.url.params["page"] == "1":
                return httpx.Response(200, json={"products": [{"id": i} for i in range(250)]})
            return httpx.Response(200, json={"products": page2})
        return httpx.Response(404)

    async def go():
        async with client_for(handler) as c:
            return await C.detect_currency(c, "s.example"), await C.fetch_products(c, "s.example", delay=0)
    cur, prods = run(go())
    assert cur == "TRY" and len(prods) == 260


def _png(seed: int) -> bytes:
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = ((x * seed) % 256, (y * (seed + 3)) % 256, ((x + y) * seed) % 256)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_hash_image_is_stable_and_distinguishes():
    data = {"/a.png": _png(5), "/b.png": _png(5), "/c.png": _png(37)}

    def handler(req):
        return httpx.Response(200, content=data[req.url.path])

    async def go():
        async with client_for(handler) as c:
            return [await C.hash_image(c, f"https://cdn.example{p}") for p in ("/a.png", "/b.png", "/c.png")]
    a, b, c = run(go())
    assert a == b and a != c


def _add(conn, store, pid, title, phash):
    conn.execute("INSERT INTO products(store,pid,title,price,image_url,phash,first_seen,last_seen) "
                 "VALUES (?,?,?,?,?,?,?,?)", (store, pid, title, 10.0, "u", phash, "d", "d"))


def test_cross_store_grouping_matches_near_duplicates_only():
    conn = connect(":memory:")
    base = 0xF0F0F0F0F0F0F0F0
    near = base ^ 0b101            # 2 bit fark
    far = base ^ ((1 << 32) - 1)   # 32 bit fark
    _add(conn, "a.tr", 1, "Blender", f"{base:016x}")
    _add(conn, "b.com", 2, "Karıştırıcı", f"{near:016x}")
    _add(conn, "c.com", 3, "Başka ürün", f"{far:016x}")
    _add(conn, "a.tr", 4, "Aynı mağaza kopya", f"{near:016x}")
    groups = trends.cross_store_groups(conn)
    assert len(groups) == 1
    assert groups[0]["stores"] == 2
    assert {l["store"] for l in groups[0]["listings"]} == {"a.tr", "b.com"}


def test_rank_movers():
    conn = connect(":memory:")
    for pid, title in [(1, "X"), (2, "Y"), (3, "Z")]:
        conn.execute("INSERT INTO products(store,pid,title,first_seen,last_seen) VALUES ('s',?,?,'d','d')",
                     (pid, title))
    for pid, rank in [(1, 5), (2, 1), (3, 2)]:
        conn.execute("INSERT INTO rankings VALUES ('2026-10-19','s',?,?)", (pid, rank))
    for pid, rank in [(1, 1), (2, 2), (4, 3)]:   # 1 yükseldi, 2 düştü, 4 yeni
        conn.execute("INSERT INTO rankings VALUES ('2026-10-20','s',?,?)", (pid, rank))
    movers = trends.rank_movers(conn)
    assert movers[0]["title"] == "X" and movers[0]["change"] == 4
    assert any(m["new_entry"] for m in movers)
    assert all(m["title"] != "Y" for m in movers)


def test_registrable_tr():
    assert sources.registrable_tr("www.magaza.com.tr") == "magaza.com.tr"
    assert sources.registrable_tr("api.shop.example.com.tr") == "example.com.tr"
    assert sources.registrable_tr("butik.tr") == "butik.tr"


def test_prioritize_turkish_hints():
    out = sources.prioritize(["rastgele.com", "güzelmoda.com", "butikyeni.net", "a.com.tr"])
    assert out[0] == "a.com.tr"
    assert set(out[1:3]) == {"güzelmoda.com", "butikyeni.net"}
    assert out[3] == "rastgele.com"


def test_fetch_crtsh_filters_and_survives_errors(monkeypatch):
    import datetime as dt
    today = dt.date.today().isoformat()

    def handler(req):
        q = req.url.params["q"]
        if q == "%.com.tr":
            return httpx.Response(200, json=[
                {"not_before": today + "T01:00:00", "name_value": "www.yeni.com.tr\n*.yeni.com.tr"},
                {"not_before": "2001-01-01T00:00:00", "name_value": "eski.com.tr"}])
        return httpx.Response(502)

    async def go():
        async with client_for(handler) as c:
            return await sources.fetch_crtsh(c, suffixes=("com.tr", "net.tr"), delay=0)
    assert run(go()) == ["yeni.com.tr"]


def test_looks_turkish():
    assert C.looks_turkish('<html lang="tr">', [])
    assert not C.looks_turkish('<html lang="en">', ["Shirt", "Hat"])
    assert C.looks_turkish("<html>", ["Çanta ışık", "Güneş", "Kış montu", "Bot"])
