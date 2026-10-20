"""docs/data.json içeriğini üretir (web sayfası bu dosyayı okur)."""
import datetime as dt
import sqlite3

from . import brands, trends

MAX_STORES = 300       # her durum için en yeni diğer mağaza sayısı (tüm Türk mağazalar ve markalar ayrıca eklenir)
MAX_CROSS = 60
IMAGES_PER_STORE = 3


def parse_seen(value: str) -> dt.datetime:
    """'2026-10-20' (yalnız gün) veya '2026-10-20T14:05' (UTC) -> datetime."""
    d = dt.datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def clean_image(url: str | None) -> str | None:
    """Sadece https görseller; sürüm parametresini atıp veriyi küçültür."""
    if not url or not url.startswith("https://"):
        return None
    return url.split("?", 1)[0]


def store_images(conn: sqlite3.Connection, domain: str) -> list[str]:
    """Önce çok satanlar sıralamasındaki ürünlerin görselleri, yoksa son görülenler."""
    rows = conn.execute(
        """SELECT p.image_url,
                  (SELECT MIN(r.rank) FROM rankings r WHERE r.store=p.store AND r.pid=p.pid) AS best
           FROM products p WHERE p.store=? AND p.image_url IS NOT NULL
           ORDER BY best IS NULL, best, p.last_seen DESC, p.pid LIMIT 12""", (domain,)).fetchall()
    out: list[str] = []
    for r in rows:
        img = clean_image(r["image_url"])
        if img and img not in out:
            out.append(img)
        if len(out) == IMAGES_PER_STORE:
            break
    return out


def build_report(conn: sqlite3.Connection, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    stores_all = conn.execute(
        "SELECT domain, first_seen, currency, is_tr, product_count, status FROM stores").fetchall()
    established = {r[0] for r in conn.execute("SELECT domain FROM established")}
    brand_domains = {r["domain"] for r in stores_all if brands.is_known_brand(r["domain"], established)}
    seen = {r["domain"]: parse_seen(r["first_seen"]) for r in stores_all}
    # "Yeni mağaza" sayıları bilinen markaları saymaz (onlar yeni açılmış değil, yeni keşfedilmiş)
    fresh = {d: t for d, t in seen.items() if d not in brand_domains}
    new_24h = sum(1 for t in fresh.values() if now - t <= dt.timedelta(hours=24))
    new_7d = sum(1 for t in fresh.values() if now - t <= dt.timedelta(days=7))
    tr_domains = {r["domain"] for r in stores_all if r["is_tr"]}
    products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    # Tüm Türk mağazalar + tüm markalar + her durumdan (açık / açılmak üzere) en yeni mağazalar
    ordered = sorted(stores_all, key=lambda r: (seen[r["domain"]], r["is_tr"]), reverse=True)
    must = [r for r in ordered if r["is_tr"] or r["domain"] in brand_domains]
    chosen = list(must)
    for status in ("open", "upcoming"):
        rest = [r for r in ordered if r["status"] == status and not (r["is_tr"] or r["domain"] in brand_domains)]
        chosen += rest[:MAX_STORES]
    chosen.sort(key=lambda r: (seen[r["domain"]], r["is_tr"]), reverse=True)
    new_stores = [{
        "domain": r["domain"], "first_seen": r["first_seen"], "currency": r["currency"],
        "is_tr": int(r["is_tr"]), "is_brand": int(r["domain"] in brand_domains), "status": r["status"], "product_count": r["product_count"],
        "images": store_images(conn, r["domain"]),
    } for r in chosen]

    cross = []
    for g in trends.cross_store_groups(conn)[:MAX_CROSS]:
        names = list(dict.fromkeys(l["store"] for l in g["listings"]))
        cross.append({
            "title": g["title"], "image_url": clean_image(g["image_url"]),
            "stores": g["stores"], "store_names": names[:6],
            "has_tr": any(n in tr_domains for n in names),
            "brand_only": all(n in brand_domains for n in names),
        })

    movers = trends.rank_movers(conn)
    scans = conn.execute("SELECT COUNT(DISTINCT scan_date) FROM rankings").fetchone()[0]
    return {
        "generated": now.date().isoformat(),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {"stores": len(stores_all), "tr_stores": len(tr_domains), "products": products,
                  "open": sum(1 for r in stores_all if r["status"] == "open"),
                  "upcoming": sum(1 for r in stores_all if r["status"] == "upcoming"),
                  "tr_open": sum(1 for r in stores_all if r["is_tr"] and r["status"] == "open"),
                  "tr_upcoming": sum(1 for r in stores_all if r["is_tr"] and r["status"] == "upcoming"),
                  "tab_new": sum(1 for r in stores_all if r["status"] == "open" and r["domain"] not in brand_domains),
                  "tab_upcoming": sum(1 for r in stores_all if r["status"] == "upcoming" and r["domain"] not in brand_domains),
                  "brands": len(brand_domains), "new_24h": new_24h, "new_7d": new_7d, "movers": len(movers),
                  "compare_ready": scans >= 2},
        "brand_domains": sorted(brand_domains),
        "new_stores": new_stores,
        "cross_store": cross,
        "movers": movers,
        "note": "Sıralamalar herkese açık 'çok satanlar' sayfalarından gelir; gerçek satış adedi değildir.",
    }
