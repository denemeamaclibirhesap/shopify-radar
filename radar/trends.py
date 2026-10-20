"""Mağazalar arası ürün eşleştirme ve sıralama değişimi analizi."""
import sqlite3
from collections import defaultdict

BANDS = 6        # 6 bant x 11 bit: Hamming mesafesi <= 5 ise en az bir bant birebir aynı kalır
BAND_BITS = 11
MAX_BUCKET = 400  # tek bir kovada karşılaştırılacak en fazla benzersiz görsel


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def cross_store_groups(conn: sqlite3.Connection, max_distance: int = 5, min_stores: int = 2):
    """Aynı görseli kullanan ürünleri farklı mağazalar arasında gruplar."""
    rows = conn.execute(
        "SELECT store, pid, title, price, image_url, phash FROM products WHERE phash IS NOT NULL"
    ).fetchall()
    hashes = [int(r["phash"], 16) for r in rows]
    uf = _UF(len(rows))
    # Birebir aynı görseller tek noktada birleştirilir (yer tutucu/aynı logo yüzlerce üründe tekrarlanabilir;
    # çiftleri tek tek karşılaştırmak kareli yavaşlamaya yol açıyordu).
    by_hash: dict[int, int] = {}
    for i, h in enumerate(hashes):
        if h in by_hash:
            uf.union(by_hash[h], i)
        else:
            by_hash[h] = i
    reps = list(by_hash.items())            # (hash, ilk satır)
    buckets = defaultdict(list)
    mask = (1 << BAND_BITS) - 1
    for k, (h, _) in enumerate(reps):
        for b in range(BANDS):
            buckets[(b, (h >> (b * BAND_BITS)) & mask)].append(k)
    for members in buckets.values():
        if len(members) < 2 or len(members) > MAX_BUCKET:
            continue   # çok kalabalık kovalar (anlamsız ortak görseller) atlanır
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                (hx, ix), (hy, iy) = reps[members[x]], reps[members[y]]
                if hamming(hx, hy) <= max_distance:
                    uf.union(ix, iy)

    groups = defaultdict(list)
    for i in range(len(rows)):
        groups[uf.find(i)].append(rows[i])
    out = []
    for items in groups.values():
        stores = {r["store"] for r in items}
        if len(stores) >= min_stores:
            out.append({
                "stores": len(stores),
                "title": items[0]["title"],
                "image_url": items[0]["image_url"],
                "listings": [{"store": r["store"], "title": r["title"], "price": r["price"]}
                             for r in items],
            })
    out.sort(key=lambda g: g["stores"], reverse=True)
    return out


def _img(row) -> str | None:
    url = row["image_url"]
    return url.split("?", 1)[0] if url and url.startswith("https://") else None


def rank_movers(conn: sqlite3.Connection, top: int = 50):
    """Son iki tarama arasında çok satanlar sıralamasında yükselenler."""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT scan_date FROM rankings ORDER BY scan_date DESC LIMIT 2")]
    if len(dates) < 2:
        return []
    cur, prev = dates
    rows = conn.execute(
        """SELECT c.store, c.pid, c.rank AS cur_rank, p.rank AS prev_rank, pr.title,
                  pr.image_url, pr.handle
           FROM rankings c
           LEFT JOIN rankings p ON p.scan_date=? AND p.store=c.store AND p.pid=c.pid
           LEFT JOIN products pr ON pr.store=c.store AND pr.pid=c.pid
           WHERE c.scan_date=?""", (prev, cur)).fetchall()
    movers = []
    for r in rows:
        if r["prev_rank"] is None:
            movers.append({"store": r["store"], "title": r["title"], "change": None,
                           "rank": r["cur_rank"], "new_entry": True,
                           "image_url": _img(r), "handle": r["handle"]})
        elif r["prev_rank"] > r["cur_rank"]:
            movers.append({"store": r["store"], "title": r["title"],
                           "change": r["prev_rank"] - r["cur_rank"],
                           "rank": r["cur_rank"], "new_entry": False,
                           "prev_rank": r["prev_rank"], "image_url": _img(r), "handle": r["handle"]})
    movers.sort(key=lambda m: (m["change"] is None, -(m["change"] or 0)))
    return movers[:top]
