"""SQLite şeması. Veritabanı dosyası repoda tutulur (data/radar.db)."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    domain TEXT PRIMARY KEY,
    seen TEXT NOT NULL,
    is_shopify INTEGER NOT NULL,
    status TEXT
);
CREATE TABLE IF NOT EXISTS stores (
    domain TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_checked TEXT,
    currency TEXT,
    is_tr INTEGER NOT NULL DEFAULT 0,
    product_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS products (
    store TEXT NOT NULL,
    pid INTEGER NOT NULL,
    title TEXT,
    handle TEXT,
    price REAL,
    image_url TEXT,
    phash TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (store, pid)
);
CREATE TABLE IF NOT EXISTS rankings (
    scan_date TEXT NOT NULL,
    store TEXT NOT NULL,
    pid INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    PRIMARY KEY (scan_date, store, pid)
);
CREATE TABLE IF NOT EXISTS established (
    domain TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_products_phash ON products(phash);
CREATE INDEX IF NOT EXISTS idx_rankings_store_pid ON rankings(store, pid);
"""


def connect(path: str = "data/radar.db") -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Eski veritabanlarına yeni sütunları ekler."""
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "status" not in cols("candidates"):
        conn.execute("ALTER TABLE candidates ADD COLUMN status TEXT")
        conn.execute("UPDATE candidates SET status='open' WHERE is_shopify=1")
    if "status" not in cols("stores"):
        conn.execute("ALTER TABLE stores ADD COLUMN status TEXT NOT NULL DEFAULT 'open'")
    conn.commit()


PRODUCT_CAP = 40   # mağaza başına saklanan en fazla ürün (veritabanı repoda tutulduğu için küçük kalmalı)


def prune_products(conn: sqlite3.Connection, cap: int = PRODUCT_CAP, vacuum: bool = True) -> int:
    """Her mağazada sıralamada olanları + en yenileri tutar, fazlasını siler. Silinen sayıyı döner."""
    conn.execute("DROP TABLE IF EXISTS temp.best")
    conn.execute("CREATE TEMP TABLE best AS SELECT store, pid, MIN(rank) AS r FROM rankings GROUP BY store, pid")
    conn.execute("CREATE INDEX temp.best_ix ON best(store, pid)")
    cur = conn.execute(
        """DELETE FROM products WHERE rowid IN (
             SELECT rowid FROM (
               SELECT p.rowid AS rowid,
                      ROW_NUMBER() OVER (PARTITION BY p.store
                                         ORDER BY b.r IS NULL, b.r, p.phash IS NULL, p.last_seen DESC, p.pid) AS n
               FROM products p LEFT JOIN temp.best b ON b.store=p.store AND b.pid=p.pid) WHERE n > ?)""", (cap,))
    removed = cur.rowcount
    conn.execute("DROP TABLE temp.best")
    conn.execute("DELETE FROM rankings WHERE NOT EXISTS "
                 "(SELECT 1 FROM products p WHERE p.store=rankings.store AND p.pid=rankings.pid)")
    conn.commit()
    if vacuum and removed > 1000:
        conn.execute("VACUUM")
    return removed
