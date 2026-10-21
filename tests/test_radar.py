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
        if q == "%moda%.com.tr":
            return httpx.Response(200, json=[
                {"not_before": today + "T01:00:00", "name_value": "www.yeni.com.tr\n*.yeni.com.tr"},
                {"not_before": "2001-01-01T00:00:00", "name_value": "eski.com.tr"}])
        return httpx.Response(502)

    async def go():
        async with client_for(handler) as c:
            return await sources.fetch_crtsh(c, patterns=("%moda%.com.tr", "%butik%.com.tr"), delay=0)
    assert run(go()) == ["yeni.com.tr"]


def test_looks_turkish():
    assert C.looks_turkish('<html lang="tr">', [])
    assert not C.looks_turkish('<html lang="en">', ["Shirt", "Hat"])
    assert C.looks_turkish("<html>", ["Çanta ışık", "Güneş", "Kış montu", "Bot"])


def test_fetch_majestic_tr_keeps_only_tr():
    csv = ("GlobalRank,TldRank,Domain,TLD,RefSubNets\n"
           "1,1,google.com,com,5\n"
           "900,12,magaza.com.tr,tr,5\n"
           "901,13,butik.tr,tr,5\n")

    async def go():
        async with client_for(lambda req: httpx.Response(200, text=csv)) as c:
            return await sources.fetch_majestic_tr(c)
    assert run(go()) == ["magaza.com.tr", "butik.tr"]


def test_majestic_due_weekly():
    import datetime as dt
    from radar import cli
    conn = connect(":memory:")
    assert cli.majestic_due(conn, False)  # established tablosu boşken liste indirilir
    conn.execute("INSERT INTO established VALUES ('a.com.tr')")
    conn.execute("DELETE FROM meta")
    conn.execute("INSERT INTO meta VALUES ('majestic_last', ?)", (dt.date.today().isoformat(),))
    assert not cli.majestic_due(conn, False)
    assert cli.majestic_due(conn, True)


def test_known_brand_detection():
    from radar import brands
    for d in ("nike.com", "nike.com.tr", "adidas.com.tr", "dagi.com.tr", "derimod.com.tr", "reebok.co.uk",
              "new-balance.com"):
        assert brands.is_known_brand(d), d
    for d in ("nike-outlet.shop", "benimbutigim.com", "yenimagaza.com.tr", "sneakers.nike-fan.com"):
        assert not brands.is_known_brand(d), d
    assert brands.is_known_brand("kucukdukkan.com", {"kucukdukkan.com"})


def test_shopify_status_open_upcoming_none():
    def handler(req):
        host = req.url.host
        if host == "open.example":
            return httpx.Response(200, json={"products": []})
        if host == "soon.example":
            if req.url.path == "/password":
                return httpx.Response(200, text='<form action="/password" method="post">Opening soon</form>')
            return httpx.Response(302, headers={"location": "https://soon.example/password"})
        if host == "html.example":
            return httpx.Response(200, text="<html>merhaba</html>")
        return httpx.Response(404)

    async def go():
        async with client_for(handler) as c:
            return [await detect.shopify_status(c, h)
                    for h in ("open.example", "soon.example", "html.example", "none.example")]
    assert run(go()) == ["open", "upcoming", None, None]


def test_discover_records_upcoming_and_backfills(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from radar import cli
    conn = connect(":memory:")
    # Eskiden "Shopify değil" diye işaretlenmiş (status NULL) bir aday: yeniden bakılmalı
    conn.execute("INSERT INTO candidates(domain, seen, is_shopify) VALUES ('eski.com.tr', '2026-10-01', 0)")

    async def fake_check(client, domains, stats=None, **kw):
        stats["shopify"] = len(domains)
        return {d: ("upcoming" if d == "eski.com.tr" else "open" if d == "yeni.com" else None) for d in domains}
    monkeypatch.setattr(cli.detect, "check_domains", fake_check)
    f = tmp_path / "d.txt"; f.write_text("yeni.com\n")
    args = SimpleNamespace(file=str(f), limit=100, time_budget=60, no_majestic=True, force_majestic=False, crtsh=False, no_cc=True, no_crux=True, cc_pages=1)
    run(cli.cmd_discover(conn, args))
    assert conn.execute("SELECT status FROM stores WHERE domain='yeni.com'").fetchone()[0] == "open"
    assert conn.execute("SELECT COUNT(*) FROM stores WHERE domain='eski.com.tr'").fetchone()[0] == 0  # --file: geri tarama yok
    args.file = None
    monkeypatch.setattr(cli, "gather_domains", lambda *a, **k: _async([]))
    run(cli.cmd_discover(conn, args))
    row = conn.execute("SELECT status, is_tr FROM stores WHERE domain='eski.com.tr'").fetchone()
    assert (row["status"], row["is_tr"]) == ("upcoming", 1)
    assert conn.execute("SELECT status FROM candidates WHERE domain='eski.com.tr'").fetchone()[0] == "upcoming"


async def _async(v):
    return v


def test_fetch_commoncrawl_tr_pages_and_cursor():
    import json as _json

    def handler(req):
        if req.url.path.endswith("collinfo.json"):
            return httpx.Response(200, json=[{"id": "CC-MAIN-2026-39"}])
        if req.url.params.get("showNumPages"):
            return httpx.Response(200, json={"pages": 3})
        page = int(req.url.params["page"])
        rows = {0: ["https://www.butik.com.tr/a", "https://butik.com.tr/b", "https://shop.deneme.com.tr/"],
                1: ["https://moda.com.tr/x"], 2: ["https://yok.example.com/x"]}[page]
        return httpx.Response(200, text="\n".join(_json.dumps({"url": u}) for u in rows))

    async def go(cursor, pages):
        async with client_for(handler) as c:
            return await sources.fetch_commoncrawl_tr(c, cursor, pages=pages, delay=0)
    domains, cur = run(go(None, 1))
    assert domains == ["butik.com.tr", "deneme.com.tr"] or set(domains) == {"butik.com.tr", "deneme.com.tr"}
    assert cur["page"] == 1
    domains, cur = run(go(cur, 5))           # kalınan yerden devam, dizin sonunda durur
    assert domains == ["moda.com.tr"] and cur["page"] == 3


def test_dns_class_shopify_cloudflare_none():
    class Rd:
        def __init__(self, a): self.address = a

    class Ans(list):
        canonical_name = "x.example."

    class FakeResolver:
        async def resolve(self, name, kind):
            table = {"s.example": "23.227.38.65", "cf.example": "104.21.5.5", "www.cf2.example": "172.67.1.1",
                     "other.example": "93.184.216.34"}
            if name in table:
                return Ans([Rd(table[name])])
            raise OSError("nxdomain")
    r = FakeResolver()
    got = [run(detect.dns_class(d, r)) for d in ("s.example", "cf.example", "cf2.example", "other.example", "yok.example")]
    assert got == ["shopify", "cloudflare", "cloudflare", None, None]


def test_registrable_and_crux(monkeypatch):
    import gzip
    assert sources.registrable("www.magaza.com") == "magaza.com"
    assert sources.registrable("shop.example.co.uk") == "example.co.uk"
    assert sources.registrable("a.b.com.tr") == "b.com.tr"
    csv = "origin,rank\nhttps://www.butik.com,1000\nhttps://butik.com,10000\nhttps://x.y.com.tr,100000\nhttp://localhost,1000\n"
    blob = gzip.compress(csv.encode())

    def handler(req):
        return httpx.Response(200, content=blob) if req.url.path.endswith(".csv.gz") else httpx.Response(404)

    async def go():
        async with client_for(handler) as c:
            return await sources.fetch_crux(c)
    assert run(go()) == ["butik.com", "y.com.tr"]


def test_fetch_products_skips_broken_records():
    def handler(req):
        return httpx.Response(200, json={"products": [{"id": 1}, {"title": "kimliksiz"}, "bozuk", {"id": 2}]})

    async def go():
        async with client_for(handler) as c:
            return await C.fetch_products(c, "x.example")
    assert [p["id"] for p in run(go())] == [1, 2]
