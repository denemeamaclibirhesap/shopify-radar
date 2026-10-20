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
            items = r.json().get("products", [])
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


async def detect_currency(client: httpx.AsyncClient, domain: str) -> str | None:
    try:
        r = await client.get(f"https://{domain}/")
        m = CURRENCY_RE.search(r.text)
        return m.group(1) if m else None
    except httpx.HTTPError:
        return None


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
