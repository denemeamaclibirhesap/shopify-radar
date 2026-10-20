"""SQLite şeması. Veritabanı dosyası repoda tutulur (data/radar.db)."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    domain TEXT PRIMARY KEY,
    seen TEXT NOT NULL,
    is_shopify INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stores (
    domain TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_checked TEXT,
    currency TEXT,
    is_tr INTEGER NOT NULL DEFAULT 0,
    product_count INTEGER NOT NULL DEFAULT 0
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
CREATE INDEX IF NOT EXISTS idx_products_phash ON products(phash);
"""


def connect(path: str = "data/radar.db") -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
