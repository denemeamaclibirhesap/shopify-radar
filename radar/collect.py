"""Mağazalardan herkese açık ürün, sıralama ve para birimi verisi toplar."""
import asyncio
import io
import re

import httpx
import imagehash
from PIL import Image

CURRENCY_RE = re.compile(r'Shopify\.currency\s*=\s*\{\s*"active"\s*:\s*"([A-Z]{3})"')


async def fetch_products(client: httpx.AsyncClient, domain: str, max_pages: int = 4,
                         delay: float = 0.5) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            r = await client.get(f"https://{domain}/products.json",
                                 params={"limit": 250, "page": page})
            r.raise_for_status()
            items = [p for p in r.json().get("products", [])
                     if isinstance(p, dict) and isinstance(p.get("id"), int)]   # bozuk kayıtları ele
        except (httpx.HTTPError, ValueError):
            break
        out.extend(items)
        if len(items) < 250:
            break
        await asyncio.sleep(delay)
    return out


async def fetch_bestsellers(client: httpx.AsyncClient, domain: str, limit: int = 50) -> list[int]:
    """Herkese açık 'çok satanlar' sıralaması (gerçek satış adedi DEĞİL)."""
    try:
        r = await client.get(f"https://{domain}/collections/all/products.json",
                            params={"sort_by": "best-selling", "limit": limit})
        r.raise_for_status()
        return [p["id"] for p in r.json().get("products", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


HTML_LANG_TR_RE = re.compile(r'<html[^>]*\blang=["\']tr\b', re.I)
LOCALE_TR_RE = re.compile(r'Shopify\.locale\s*=\s*"tr', re.I)
TR_TITLE_LETTERS = set("ğışİ")


async def fetch_home(client: httpx.AsyncClient, domain: str) -> str:
    try:
        return (await client.get(f"https://{domain}/")).text
    except httpx.HTTPError:
        return ""


def parse_currency(html: str) -> str | None:
    m = CURRENCY_RE.search(html)
    return m.group(1) if m else None


def looks_turkish(html: str, titles: list[str]) -> bool:
    """Para birimi TRY olmasa da Türkçe satan mağazayı yakalar (sayfa dili / ürün adları)."""
    if HTML_LANG_TR_RE.search(html) or LOCALE_TR_RE.search(html):
        return True
    titles = [t for t in titles if t]
    if not titles:
        return False
    hits = sum(1 for t in titles if any(c in TR_TITLE_LETTERS for c in t))
    return hits >= 3 and hits / len(titles) >= 0.2


async def detect_currency(client: httpx.AsyncClient, domain: str) -> str | None:
    return parse_currency(await fetch_home(client, domain))


async def hash_image(client: httpx.AsyncClient, url: str) -> str | None:
    """Görselin pHash'i (64 bit, hex). Görsel saklanmaz."""
    try:
        sep = "&" if "?" in url else "?"
        r = await client.get(f"{url}{sep}width=200")
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return None


def first_price(product: dict) -> float | None:
    try:
        return float(product["variants"][0]["price"])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def first_image(product: dict) -> str | None:
    imgs = product.get("images") or []
    return imgs[0].get("src") if imgs else None
