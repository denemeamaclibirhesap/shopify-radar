"""Shopify tespiti: önce ucuz DNS filtresi, sonra HTTP doğrulaması."""
import asyncio
import ipaddress

import dns.asyncresolver
import dns.exception
import httpx

SHOPIFY_IP_PREFIX = "23.227.38."


def make_resolver() -> dns.asyncresolver.Resolver:
    r = dns.asyncresolver.Resolver()
    r.timeout = 3
    r.lifetime = 4
    return r


# Cloudflare arkasındaki mağazaların DNS kaydı Shopify'ı göstermez; bunlar da HTTP ile denenir.
CLOUDFLARE_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22")]


def _is_cloudflare(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in CLOUDFLARE_NETS)


async def dns_class(domain: str, resolver=None) -> str | None:
    """'shopify' (Shopify IP/CNAME), 'cloudflare' (Cloudflare arkasında, HTTP ile bakılmalı) ya da None."""
    resolver = resolver or make_resolver()
    found = None
    for name in (domain, f"www.{domain}"):
        try:
            ans = await resolver.resolve(name, "A")
        except (dns.exception.DNSException, OSError):
            continue
        if str(ans.canonical_name).rstrip(".").endswith("myshopify.com"):
            return "shopify"
        if any(rd.address.startswith(SHOPIFY_IP_PREFIX) for rd in ans):
            return "shopify"
        if any(_is_cloudflare(rd.address) for rd in ans):
            found = "cloudflare"
    return found


async def dns_looks_shopify(domain: str, resolver=None) -> bool:
    return await dns_class(domain, resolver) == "shopify"


async def verify_shopify(client: httpx.AsyncClient, domain: str) -> bool:
    """/products.json açıksa ve beklenen yapıdaysa mağaza Shopify'dır."""
    try:
        r = await client.get(f"https://{domain}/products.json", params={"limit": 1})
        if r.status_code != 200:
            return False
        data = r.json()
        return isinstance(data, dict) and isinstance(data.get("products"), list)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        return False


def is_password_page(r: httpx.Response) -> bool:
    """Shopify'ın 'mağaza şifreli / yakında açılıyor' sayfası."""
    if r.url.path.rstrip("/") == "/password":
        return True
    return 'action="/password"' in r.text[:300000]


def _why(r: httpx.Response, domain: str) -> str:
    """Doğrulanamayan site neden Shopify mağazası sayılmadı? (tanı amaçlı kısa etiket)"""
    if r.url.host and r.url.host.removeprefix("www.") != domain.removeprefix("www."):
        return "baska-alana-yonlendi"
    if r.status_code != 200:
        return f"http{r.status_code}"
    text = r.text[:200000].lower()
    if "currently unavailable" in text or "sorry, this shop" in text:
        return "magaza-kapali"
    return "200-html-shopify" if "shopify" in text else "200-html"


async def shopify_status(client: httpx.AsyncClient, domain: str, reasons=None) -> str | None:
    """'open' (ürün listesi açık), 'upcoming' (şifreli / henüz yayında değil) ya da None.

    `reasons` (Counter) verilirse doğrulanamama nedenleri sayılır.
    """
    why = "hata"
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
        why = _why(r, domain)
    except httpx.TimeoutException:
        why = "zaman-asimi"
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        why = type(e).__name__
    except Exception as e:  # noqa: BLE001 - tek bir bozuk site tüm taramayı durdurmasın
        why = f"beklenmeyen-{type(e).__name__}"
    if reasons is not None:
        reasons[why] += 1
    return None


async def check_domains(client, domains, dns_concurrency=300, http_concurrency=80, stats=None):
    """Dönüş: {domain: 'open' | 'upcoming' | None}. `stats` DNS'te 'shopify' ve 'cloudflare' görünenleri sayar."""
    resolver = make_resolver()
    dns_sem = asyncio.Semaphore(dns_concurrency)
    http_sem = asyncio.Semaphore(http_concurrency)
    results: dict[str, str | None] = {}
    stats = stats if stats is not None else {}

    async def one(d: str):
        async with dns_sem:
            kind = await dns_class(d, resolver)
        if not kind:
            results[d] = None
            return
        stats[kind] = stats.get(kind, 0) + 1
        async with http_sem:
            results[d] = await shopify_status(client, d, stats.get("reasons"))

    await asyncio.gather(*(one(d) for d in domains))
    return results
