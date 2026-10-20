"""Shopify tespiti: önce ucuz DNS filtresi, sonra HTTP doğrulaması."""
import asyncio

import dns.asyncresolver
import dns.exception
import httpx

SHOPIFY_IP_PREFIX = "23.227.38."


def make_resolver() -> dns.asyncresolver.Resolver:
    r = dns.asyncresolver.Resolver()
    r.timeout = 3
    r.lifetime = 4
    return r


async def dns_looks_shopify(domain: str, resolver=None) -> bool:
    resolver = resolver or make_resolver()
    for name in (domain, f"www.{domain}"):
        try:
            ans = await resolver.resolve(name, "A")
        except (dns.exception.DNSException, OSError):
            continue
        if str(ans.canonical_name).rstrip(".").endswith("myshopify.com"):
            return True
        if any(rd.address.startswith(SHOPIFY_IP_PREFIX) for rd in ans):
            return True
    return False


async def verify_shopify(client: httpx.AsyncClient, domain: str) -> bool:
    """/products.json açıksa ve beklenen yapıdaysa mağaza Shopify'dır."""
    try:
        r = await client.get(f"https://{domain}/products.json", params={"limit": 1})
        if r.status_code != 200:
            return False
        data = r.json()
        return isinstance(data, dict) and isinstance(data.get("products"), list)
    except (httpx.HTTPError, ValueError):
        return False


def is_password_page(r: httpx.Response) -> bool:
    """Shopify'ın 'mağaza şifreli / yakında açılıyor' sayfası."""
    if r.url.path.rstrip("/") == "/password":
        return True
    return 'action="/password"' in r.text[:300000]


async def shopify_status(client: httpx.AsyncClient, domain: str) -> str | None:
    """'open' (ürün listesi açık), 'upcoming' (şifreli / henüz yayında değil) ya da None."""
    try:
        r = await client.get(f"https://{domain}/products.json", params={"limit": 1})
        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and isinstance(data.get("products"), list):
                    return "open"
            except ValueError:
                pass
            if is_password_page(r):
                return "upcoming"
        elif r.status_code in (401, 403) and is_password_page(r):
            return "upcoming"
    except httpx.HTTPError:
        pass
    return None


async def check_domains(client, domains, dns_concurrency=300, http_concurrency=20, stats=None):
    """Dönüş: {domain: 'open' | 'upcoming' | None}. `stats['dns']` DNS'te Shopify görünenleri sayar."""
    resolver = make_resolver()
    dns_sem = asyncio.Semaphore(dns_concurrency)
    http_sem = asyncio.Semaphore(http_concurrency)
    results: dict[str, str | None] = {}
    stats = stats if stats is not None else {}

    async def one(d: str):
        async with dns_sem:
            ok = await dns_looks_shopify(d, resolver)
        if not ok:
            results[d] = None
            return
        stats["dns"] = stats.get("dns", 0) + 1
        async with http_sem:
            results[d] = await shopify_status(client, d)

    await asyncio.gather(*(one(d) for d in domains))
    return results
