"""Komut satırı: python -m radar.cli discover | collect | report | run"""
import argparse
import collections
import asyncio
import datetime as dt
import json
import time
from pathlib import Path

import httpx

from . import collect as C
from . import detect, report, sources
from .db import connect

CHUNK = 5000  # her turdan sonra kaydedilir, süre dolarsa o ana kadarki iş kaybolmaz
UA = "ShopifyRadar/0.1 (herkese acik veri arastirmasi)"


def today() -> str:
    return dt.date.today().isoformat()


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15, follow_redirects=True,
                             limits=httpx.Limits(max_connections=60))


def majestic_due(conn, force: bool) -> bool:
    """Büyük liste haftada bir indirilir (adaylar zaten kayıtlı olduğundan her gün gerekmez)."""
    if force or conn.execute("SELECT 1 FROM established LIMIT 1").fetchone() is None:
        return True
    row = conn.execute("SELECT value FROM meta WHERE key='majestic_last'").fetchone()
    return not row or (dt.date.today() - dt.date.fromisoformat(row[0])).days >= 7


async def gather_domains(conn, client, args) -> list[str]:
    """Tüm kaynaklardan alan adı topla; bir kaynak çökerse diğerleri devam eder."""
    if args.file:
        return sources.read_file(args.file)
    domains: list[str] = []
    day = dt.date.today() - dt.timedelta(days=1)
    try:
        domains += await sources.fetch_whoisds(client, day)
    except Exception as e:  # noqa: BLE001 - kaynak çökse de tarama sürmeli
        print(f"WhoisDS atlandı: {type(e).__name__}")
    if not args.no_majestic and majestic_due(conn, args.force_majestic):
        try:
            tr = await sources.fetch_majestic_tr(client)
            print(f"Majestic: {len(tr)} .tr alan adı")
            domains += tr
            conn.executemany("INSERT OR IGNORE INTO established VALUES (?)", [(d,) for d in tr])
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('majestic_last', ?)", (today(),))
            conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"Majestic atlandı: {type(e).__name__}")
    if not args.no_crux:
        try:
            crux = await sources.fetch_crux(client)
            print(f"CrUX Türkiye: {len(crux)} alan adı")
            domains += crux
        except Exception as e:  # noqa: BLE001
            print(f"CrUX atlandı: {type(e).__name__}")
    if not args.no_cc:
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='cc_cursor'").fetchone()
            cc, cursor = await sources.fetch_commoncrawl_tr(client, json.loads(row[0]) if row else None,
                                                            pages=args.cc_pages)
            domains += cc
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('cc_cursor', ?)", (json.dumps(cursor),))
            conn.commit()
        except Exception as e:  # noqa: BLE001
            print(f"Common Crawl atlandı: {type(e).__name__}")
    if args.crtsh:  # crt.sh şu an sık sık 502 veriyor: varsayılan kapalı
        try:
            domains += await sources.fetch_crtsh(client)
        except Exception as e:  # noqa: BLE001
            print(f"crt.sh atlandı: {type(e).__name__}")
    return list(dict.fromkeys(domains))


async def cmd_discover(conn, args):
    async with new_client() as client:
        domains = await gather_domains(conn, client, args)
        known = {r[0] for r in conn.execute("SELECT domain FROM candidates WHERE status IS NOT NULL")}
        old = {r[0] for r in conn.execute("SELECT domain FROM candidates")}
        todo = [d for d in sources.prioritize(domains) if d not in old][: args.limit]
        if not args.file:
            # Eskiden "Shopify değil" sayılanlar arasında şifreli (açılmak üzere) mağazalar kaçmış olabilir:
            # bir kez yeniden bakılır, süre dolarsa kalanı sonraki taramaya bırakılır.
            again = [r[0] for r in conn.execute(
                "SELECT domain FROM candidates WHERE status IS NULL AND is_shopify=0")]
            todo += [d for d in sources.prioritize(again) if d not in known][: max(0, args.limit - len(todo))]
        n_tr = sum(d.endswith(".tr") for d in todo)
        print(f"{len(domains)} domain okundu, {len(todo)} tanesi taranacak ({n_tr} tanesi .tr)")
        now, done, start = today(), 0, time.monotonic()
        counts = {"open": 0, "upcoming": 0, "open_tr": 0, "upcoming_tr": 0}
        funnel = {"shopify": 0, "cloudflare": 0, "reasons": collections.Counter()}
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")  # "son 24 saat" için dakika hassasiyeti
        for i in range(0, len(todo), CHUNK):
            if time.monotonic() - start > args.time_budget:
                print(f"süre doldu: {done}/{len(todo)} taranabildi, kalanı sonraki taramaya")
                break
            results = await detect.check_domains(client, todo[i:i + CHUNK], stats=funnel)
            for d, status in results.items():
                conn.execute("INSERT OR REPLACE INTO candidates(domain, seen, is_shopify, status) VALUES (?,?,?,?)",
                             (d, now, int(status == "open"), status or "none"))
                if status:
                    counts[status] += 1
                    counts[status + "_tr"] += int(d.endswith(".tr"))
                    conn.execute("INSERT OR IGNORE INTO stores(domain, first_seen, is_tr, status) VALUES (?,?,?,?)",
                                 (d, stamp, int(d.endswith(".tr")), status))
            conn.commit()
            done += len(results)
    print(f"tarama özeti: {done} alan adı bakıldı -> {funnel['shopify']} tanesi DNS'te Shopify, {funnel['cloudflare']} tanesi Cloudflare arkasında göründü -> "
          f"{counts['open']} açık mağaza ({counts['open_tr']} .tr), "
          f"{counts['upcoming']} açılmak üzere ({counts['upcoming_tr']} .tr), "
          f"{funnel['shopify'] + funnel['cloudflare'] - counts['open'] - counts['upcoming']} doğrulanamadı")
    print(f"doğrulanamama nedenleri: {dict(funnel['reasons'].most_common(10))}")
    print(f"{counts['open'] + counts['upcoming']} yeni Shopify mağazası bulundu")


async def collect_store(client, conn, domain, sem, max_images, deadline=None):
    async with sem:
        if deadline is not None and time.monotonic() > deadline:
            return 0   # süre doldu: bu mağaza sonraki taramaya kalır
        try:
            return await _collect_store(client, conn, domain, max_images)
        finally:
            conn.commit()   # her mağazadan sonra kaydet: iş yarıda kesilse bile ilerleme kaybolmaz


async def _collect_store(client, conn, domain, max_images):
    now = today()
    home = await C.fetch_home(client, domain)
    currency = C.parse_currency(home)
    products = await C.fetch_products(client, domain)
    if not products:
        # Şifreli / henüz ürünü olmayan mağaza: yine de Türkiye işaretini (.tr, TRY, sayfa dili) güncelle
        tr = int(currency == "TRY" or domain.endswith(".tr") or C.looks_turkish(home, []))
        conn.execute("UPDATE stores SET last_checked=?, currency=COALESCE(?, currency), "
                     "is_tr=MAX(is_tr, ?) WHERE domain=?", (now, currency, tr, domain))
        return 0
    best = await C.fetch_bestsellers(client, domain)
    is_tr = int(currency == "TRY" or domain.endswith(".tr")
                or C.looks_turkish(home, [p.get("title") for p in products]))
    conn.execute("UPDATE stores SET last_checked=?, currency=?, is_tr=?, product_count=?, status='open' "
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
    deadline = time.monotonic() + args.collect_budget
    async with new_client() as client:
        res = await asyncio.gather(
            *(collect_store(client, conn, r["domain"], sem, args.max_images, deadline) for r in rows))
    conn.commit()
    print(f"{len(rows)} mağaza sırada, {sum(res)} ürün görüldü"
          + (" (süre doldu, kalanı sonraki taramada)" if time.monotonic() > deadline else ""))


def cmd_report(conn, args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = report.build_report(conn)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                   encoding="utf-8")
    print(f"rapor yazıldı: {out / 'data.json'}")


def main():
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--db", default="data/radar.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--file")
    d.add_argument("--limit", type=int, default=150000)
    d.add_argument("--time-budget", type=int, default=540, help="keşif için saniye")
    d.add_argument("--crtsh", action="store_true", help="crt.sh kaynağını da dene")
    d.add_argument("--no-majestic", action="store_true")
    d.add_argument("--no-crux", action="store_true", help="CrUX Türkiye listesini atla")
    d.add_argument("--no-cc", action="store_true", help="Common Crawl kaynağını atla")
    d.add_argument("--cc-pages", type=int, default=20, help="her taramada okunacak Common Crawl sayfası")
    d.add_argument("--force-majestic", action="store_true")
    c = sub.add_parser("collect")
    c.add_argument("--max-stores", type=int, default=600)
    c.add_argument("--max-images", type=int, default=25)
    c.add_argument("--collect-budget", type=int, default=360, help="toplama için saniye")
    r = sub.add_parser("report")
    r.add_argument("--out", default="docs")
    run = sub.add_parser("run")
    run.add_argument("--file")
    run.add_argument("--limit", type=int, default=150000)
    run.add_argument("--time-budget", type=int, default=540, help="keşif için saniye")
    run.add_argument("--crtsh", action="store_true", help="crt.sh kaynağını da dene")
    run.add_argument("--no-majestic", action="store_true")
    run.add_argument("--no-crux", action="store_true", help="CrUX Türkiye listesini atla")
    run.add_argument("--no-cc", action="store_true", help="Common Crawl kaynağını atla")
    run.add_argument("--cc-pages", type=int, default=20, help="her taramada okunacak Common Crawl sayfası")
    run.add_argument("--force-majestic", action="store_true")
    run.add_argument("--max-stores", type=int, default=600)
    run.add_argument("--max-images", type=int, default=25)
    run.add_argument("--collect-budget", type=int, default=360, help="toplama için saniye")
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
