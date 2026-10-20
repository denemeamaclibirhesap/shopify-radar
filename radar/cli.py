"""Komut satırı: python -m radar.cli discover | collect | report | run"""
import argparse
import asyncio
import datetime as dt
import json
from pathlib import Path

import httpx

from . import collect as C
from . import detect, sources, trends
from .db import connect

UA = "ShopifyRadar/0.1 (herkese acik veri arastirmasi)"


def today() -> str:
    return dt.date.today().isoformat()


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15, follow_redirects=True,
                             limits=httpx.Limits(max_connections=60))


async def cmd_discover(conn, args):
    async with new_client() as client:
        if args.file:
            domains = sources.read_file(args.file)
        else:
            day = dt.date.today() - dt.timedelta(days=1)
            domains = await sources.fetch_whoisds(client, day)
        known = {r[0] for r in conn.execute("SELECT domain FROM candidates")}
        todo = [d for d in sources.prioritize(domains) if d not in known][: args.limit]
        print(f"{len(domains)} domain okundu, {len(todo)} tanesi taranacak")
        results = await detect.check_domains(client, todo)
    now = today()
    found = 0
    for d, is_shop in results.items():
        conn.execute("INSERT OR REPLACE INTO candidates VALUES (?,?,?)", (d, now, int(is_shop)))
        if is_shop:
            found += 1
            conn.execute("INSERT OR IGNORE INTO stores(domain, first_seen, is_tr) VALUES (?,?,?)",
                         (d, now, int(d.endswith(".tr"))))
    conn.commit()
    print(f"{found} yeni Shopify mağazası bulundu")


async def collect_store(client, conn, domain, sem, max_images):
    async with sem:
        now = today()
        currency = await C.detect_currency(client, domain)
        products = await C.fetch_products(client, domain)
        if not products:
            conn.execute("UPDATE stores SET last_checked=? WHERE domain=?", (now, domain))
            return 0
        best = await C.fetch_bestsellers(client, domain)
        is_tr = int(currency == "TRY" or domain.endswith(".tr"))
        conn.execute("UPDATE stores SET last_checked=?, currency=?, is_tr=?, product_count=? "
                     "WHERE domain=?", (now, currency, is_tr, len(products), domain))
        have_hash = {r["pid"] for r in conn.execute(
            "SELECT pid FROM products WHERE store=? AND phash IS NOT NULL", (domain,))}
        for p in products:
            conn.execute(
                """INSERT INTO products(store,pid,title,handle,price,image_url,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(store,pid) DO UPDATE SET title=excluded.title,
                   price=excluded.price, image_url=excluded.image_url, last_seen=excluded.last_seen""",
                (domain, p["id"], p.get("title"), p.get("handle"), C.first_price(p),
                 C.first_image(p), now, now))
        for rank, pid in enumerate(best, 1):
            conn.execute("INSERT OR REPLACE INTO rankings VALUES (?,?,?,?)", (now, domain, pid, rank))
        # Önce çok satanların görsellerini hashle
        order = best + [p["id"] for p in products if p["id"] not in set(best)]
        by_id = {p["id"]: p for p in products}
        hashed = 0
        for pid in order:
            if hashed >= max_images:
                break
            if pid in have_hash or pid not in by_id:
                continue
            url = C.first_image(by_id[pid])
            if not url:
                continue
            h = await C.hash_image(client, url)
            if h:
                conn.execute("UPDATE products SET phash=? WHERE store=? AND pid=?", (h, domain, pid))
                hashed += 1
            await asyncio.sleep(0.2)
        return len(products)


async def cmd_collect(conn, args):
    rows = conn.execute(
        "SELECT domain FROM stores WHERE last_checked IS NULL OR last_checked < ? "
        "ORDER BY is_tr DESC, last_checked IS NOT NULL, first_seen DESC LIMIT ?",
        (today(), args.max_stores)).fetchall()
    sem = asyncio.Semaphore(15)
    async with new_client() as client:
        res = await asyncio.gather(
            *(collect_store(client, conn, r["domain"], sem, args.max_images) for r in rows))
    conn.commit()
    print(f"{len(rows)} mağaza işlendi, {sum(res)} ürün görüldü")


def cmd_report(conn, args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    total = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    total_tr = conn.execute("SELECT COUNT(*) FROM stores WHERE is_tr=1").fetchone()[0]
    prods = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    new_stores = [dict(r) for r in conn.execute(
        "SELECT domain, first_seen, currency, is_tr, product_count FROM stores "
        "ORDER BY first_seen DESC, is_tr DESC LIMIT 100")]
    data = {
        "generated": today(),
        "stats": {"stores": total, "tr_stores": total_tr, "products": prods},
        "new_stores": new_stores,
        "cross_store": trends.cross_store_groups(conn)[:100],
        "movers": trends.rank_movers(conn),
        "note": "Sıralamalar herkese açık 'çok satanlar' sayfalarından gelir; gerçek satış adedi değildir.",
    }
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"rapor yazıldı: {out / 'data.json'}")


def main():
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--db", default="data/radar.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--file")
    d.add_argument("--limit", type=int, default=20000)
    c = sub.add_parser("collect")
    c.add_argument("--max-stores", type=int, default=300)
    c.add_argument("--max-images", type=int, default=40)
    r = sub.add_parser("report")
    r.add_argument("--out", default="docs")
    run = sub.add_parser("run")
    run.add_argument("--file")
    run.add_argument("--limit", type=int, default=20000)
    run.add_argument("--max-stores", type=int, default=300)
    run.add_argument("--max-images", type=int, default=40)
    run.add_argument("--out", default="docs")
    args = ap.parse_args()

    conn = connect(args.db)
    if args.cmd in ("discover", "run"):
        asyncio.run(cmd_discover(conn, args))
    if args.cmd in ("collect", "run"):
        asyncio.run(cmd_collect(conn, args))
    if args.cmd in ("report", "run"):
        cmd_report(conn, args)


if __name__ == "__main__":
    main()
