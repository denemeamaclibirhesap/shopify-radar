import asyncio
import io
import json
from types import SimpleNamespace

import httpx
from PIL import Image

from radar import cli
from radar.db import connect


def _png(seed):
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = ((x * seed) % 256, (y * (seed + 3)) % 256, ((x + y) * seed) % 256)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def fake_store(host, currency, ids, img_seed):
    def handler(req):
        if req.url.host == "cdn.test":
            return httpx.Response(200, content=_png(img_seed))
        if req.url.path == "/":
            return httpx.Response(200, text='Shopify.currency = {"active":"%s","rate":"1.0"}' % currency)
        products = [{"id": i, "title": f"P{i}", "handle": f"p{i}", "variants": [{"price": "99.00"}],
                     "images": [{"src": "https://cdn.test/i.png"}]} for i in ids]
        if req.url.path == "/collections/all/products.json":
            return httpx.Response(200, json={"products": products[::-1]})
        return httpx.Response(200, json={"products": products})
    return handler


def test_collect_and_report_end_to_end(tmp_path, monkeypatch):
    handlers = {"a.com.tr": fake_store("a", "TRY", [1, 2, 3], 5),
                "b.com": fake_store("b", "USD", [7, 8], 5)}

    def router(req):
        if req.url.host == "cdn.test":
            return httpx.Response(200, content=_png(5))
        return handlers[req.url.host](req)

    monkeypatch.setattr(cli, "new_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(router), follow_redirects=True))
    real_sleep = asyncio.sleep

    async def fast_sleep(*_):
        await real_sleep(0)
    monkeypatch.setattr(cli.asyncio, "sleep", fast_sleep)

    conn = connect(":memory:")
    for d in handlers:
        conn.execute("INSERT INTO stores(domain, first_seen) VALUES (?, '2026-10-19')", (d,))
    asyncio.run(cli.cmd_collect(conn, SimpleNamespace(max_stores=10, max_images=5, collect_budget=60)))

    tr = conn.execute("SELECT is_tr, currency, product_count FROM stores WHERE domain='a.com.tr'").fetchone()
    assert (tr["is_tr"], tr["currency"], tr["product_count"]) == (1, "TRY", 3)
    assert conn.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM products WHERE phash IS NOT NULL").fetchone()[0] == 5

    cli.cmd_report(conn, SimpleNamespace(out=str(tmp_path)))
    data = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    stats = data["stats"]
    assert (stats["stores"], stats["tr_stores"], stats["products"]) == (2, 1, 5)
    assert data["generated_at"].endswith("Z") and stats["compare_ready"] is False
    tr_card = next(s for s in data["new_stores"] if s["domain"] == "a.com.tr")
    assert tr_card["is_tr"] == 1 and tr_card["images"] == ["https://cdn.test/i.png"]
    assert data["cross_store"], "aynı görsel iki mağazada eşleşmeli"
