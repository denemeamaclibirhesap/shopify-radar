"""Yeni alan adı kaynakları.

Ücretsiz kaynaklar:
  * WhoisDS günlük "newly registered domains" listesi (zip içinde txt)
  * crt.sh sertifika kayıtları: yakın zamanda sertifika alan .tr alan adları
  * Elle hazırlanmış bir domains.txt dosyası (Certstream/crt.sh çıktısı vb.)
"""
import asyncio
import base64
import datetime as dt
import io
import json
import re
import time
import zipfile
from pathlib import Path

import httpx

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$")


def whoisds_url(day: dt.date) -> str:
    token = base64.b64encode(f"{day.isoformat()}.zip".encode()).decode()
    return f"https://whoisds.com/whois-database/newly-registered-domains/{token}/nrd"


def parse_domains(text: str) -> list[str]:
    seen, out = set(), []
    for line in text.splitlines():
        d = line.strip().lower().lstrip("*.")
        if d and d not in seen and DOMAIN_RE.match(d):
            seen.add(d)
            out.append(d)
    return out


async def fetch_whoisds(client: httpx.AsyncClient, day: dt.date) -> list[str]:
    r = await client.get(whoisds_url(day), timeout=60)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        name = zf.namelist()[0]
        return parse_domains(zf.read(name).decode("utf-8", "ignore"))


def read_file(path: str) -> list[str]:
    return parse_domains(Path(path).read_text(encoding="utf-8", errors="ignore"))


TR_SUFFIXES = ("com.tr", "net.tr", "org.tr", "gen.tr", "web.tr", "biz.tr", "info.tr", "tr")
# .tr altında üçüncü seviye kayıt yapılabilen ikinci seviye uzantılar
TR_SECOND_LEVEL = {"com", "net", "org", "gen", "web", "biz", "info", "name", "tv", "bbs",
                   "kep", "av", "dr", "tel", "k12", "edu", "gov", "bel", "pol", "mil"}
TR_LETTERS = set("çğıöşüİ")
TR_HINTS = ("butik", "moda", "giyim", "taki", "aksesuar", "kozmetik", "magaza", "dunyasi",
            "cicek", "ciftlik", "kitap", "oyuncak", "ayakkabi", "canta", "saat", "mobilya",
            "dekor", "bebek", "hediye", "terlik", "tesettur", "kadin", "erkek")


def registrable_tr(name: str) -> str:
    """shop.example.com.tr -> example.com.tr (mağaza kök alan adı)."""
    labels = name.lower().strip(".").split(".")
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) >= 3 and labels[-2] in TR_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def looks_turkish_domain(domain: str) -> bool:
    """Alan adında Türkçe harf (punycode dahil) veya tipik Türkçe mağaza kelimesi var mı?"""
    name = domain
    if "xn--" in name:
        try:
            name = name.encode("ascii").decode("idna")
        except UnicodeError:
            pass
    return any(c in TR_LETTERS for c in name) or any(h in name for h in TR_HINTS)


MAJESTIC_URL = "https://downloads.majestic.com/majestic_million.csv"


async def fetch_majestic_tr(client: httpx.AsyncClient) -> list[str]:
    """Majestic Million (ücretsiz, CC BY 3.0) listesinden .tr siteleri.

    Dosya ~80 MB: akış olarak okunur, yalnızca TLD sütunu 'tr' olan satırlar tutulur.
    """
    out: list[str] = []
    async with client.stream("GET", MAJESTIC_URL, timeout=300) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            cols = line.split(",")
            if len(cols) > 3 and cols[3] == "tr":
                out.append(cols[2])
    return parse_domains("\n".join(out))


CRT_WORDS = TR_HINTS + ("shop", "store", "market", "online", "tekstil", "elektronik", "organik")
CRT_PATTERNS = tuple(f"%{w}%.com.tr" for w in CRT_WORDS)


async def fetch_crtsh(client: httpx.AsyncClient, patterns=CRT_PATTERNS, days: int = 3,
                      delay: float = 12.0, timeout: float = 40.0, deadline: float = 300.0) -> list[str]:
    """crt.sh'den son `days` günde sertifika almış .tr alan adları.

    Tüm .com.tr'yi tek seferde istemek crt.sh'yi çökertiyor; bu yüzden Türkçe mağaza
    kelimeleriyle küçük sorgulara bölünür (crt.sh'de % her yerde joker olabilir).
    crt.sh dakikada ~5 istek sever: istekler arası bekleme var, hata olan sorgu atlanır,
    toplam süre `deadline` saniyeyi aşmaz.
    """
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    start = time.monotonic()
    found: list[str] = []
    ok = failed = 0
    for i, pattern in enumerate(patterns):
        if time.monotonic() - start > deadline:
            print(f"crt.sh süre sınırı doldu: {i}/{len(patterns)} sorgu yapıldı")
            break
        if i:
            await asyncio.sleep(delay)
        try:
            r = await client.get("https://crt.sh/", timeout=timeout,
                                 params={"q": pattern, "output": "json", "exclude": "expired"})
            r.raise_for_status()
            rows = r.json()
        except (httpx.HTTPError, ValueError) as e:
            failed += 1
            print(f"crt.sh {pattern} atlandı: {type(e).__name__}")
            continue
        ok += 1
        for row in rows:
            if str(row.get("not_before", ""))[:10] < cutoff:
                continue
            for name in str(row.get("name_value", "")).splitlines():
                name = name.strip().lower().lstrip("*.")
                if name.endswith(".tr") and "@" not in name:
                    found.append(registrable_tr(name))
    domains = parse_domains("\n".join(dict.fromkeys(found)))
    print(f"crt.sh: {ok} sorgu başarılı, {failed} başarısız, {len(domains)} .tr alan adı")
    return domains


CRUX_URL = ("https://raw.githubusercontent.com/InternetHealthReport/crux-top-lists-country/main/"
            "data/country/{cc}/{ym}.csv.gz")
SECOND_LEVEL_CC = {"co", "com", "org", "net", "gov", "ac", "edu"}


def registrable(host: str) -> str:
    """www.shop.example.com -> example.com; x.example.co.uk -> example.co.uk; x.y.com.tr -> y.com.tr."""
    host = host.lower().strip(".")
    if host.endswith(".tr"):
        return registrable_tr(host)
    parts = host.split(".")
    if host.startswith("www."):
        parts = parts[1:]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL_CC:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


async def fetch_crux(client: httpx.AsyncClient, country: str = "tr") -> list[str]:
    """CrUX (Chrome kullanıcı verisi) ülke listesi: o ülkede en çok ziyaret edilen ~1M site.

    Kaynak: InternetHealthReport/crux-top-lists-country (aylık, ücretsiz). Son 4 ayı sırayla dener.
    """
    import gzip
    from urllib.parse import urlsplit
    today = dt.date.today()
    for back in range(4):
        y, m = today.year, today.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        r = await client.get(CRUX_URL.format(cc=country, ym=f"{y}{m:02d}"), timeout=120)
        if r.status_code != 200:
            continue
        out: dict[str, None] = {}
        for line in gzip.decompress(r.content).decode("utf-8", "ignore").splitlines()[1:]:
            origin = line.rsplit(",", 1)[0]
            host = urlsplit(origin).hostname
            if host and "." in host:
                out[registrable(host)] = None
        return parse_domains("\n".join(out))
    return []


CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"


async def fetch_commoncrawl_tr(client: httpx.AsyncClient, cursor: dict | None, pages: int = 20,
                               suffix: str = "com.tr", delay: float = 1.0) -> tuple[list[str], dict]:
    """Common Crawl dizininden `suffix` altındaki sitelerin alan adları (ücretsiz, nonprofit arşiv).

    Dizin SURT sırasıyla sayfalanır; her çalışmada `pages` sayfa okunur ve kalınan yer `cursor`
    olarak döner (yeni dizin çıkarsa baştan başlar). Sayfa hata verirse orada durulur, sonraki
    çalışmada aynı sayfadan devam edilir. Dönüş: (alan adları, yeni cursor).
    """
    from urllib.parse import urlsplit
    cursor = cursor or {}
    out: list[str] = []
    try:
        r = await client.get(CC_COLLINFO, timeout=30)
        r.raise_for_status()
        index_id = r.json()[0]["id"]
        api = f"https://index.commoncrawl.org/{index_id}-index"
        r = await client.get(api, timeout=60, params={"url": suffix, "matchType": "domain",
                                                      "showNumPages": "true", "output": "json"})
        r.raise_for_status()
        total = int(r.json()["pages"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
        print(f"Common Crawl atlandı: {type(e).__name__}")
        return [], cursor
    page = cursor.get("page", 0) if cursor.get("index") == index_id and cursor.get("suffix") == suffix else 0
    if page >= total:
        page = 0
    start_page, read = page, 0
    while read < pages and page < total:
        text = None
        for attempt in range(2):
            try:
                r = await client.get(api, timeout=90, params={"url": suffix, "matchType": "domain",
                                                              "output": "json", "fl": "url", "page": page})
                r.raise_for_status()
                text = r.text
                break
            except httpx.HTTPError:
                await asyncio.sleep(delay * 3)
        if text is None:
            print(f"Common Crawl sayfa {page} okunamadı, sonraki taramada denenecek")
            break
        hosts = set()
        for line in text.splitlines():
            try:
                host = urlsplit(json.loads(line)["url"]).hostname
            except (ValueError, KeyError, TypeError):
                continue
            if host and host.endswith(".tr"):
                hosts.add(registrable_tr(host))
        out += hosts
        page += 1
        read += 1
        await asyncio.sleep(delay)
    print(f"Common Crawl: sayfa {start_page}-{page - 1}/{total}, {len(set(out))} {suffix} alan adı")
    return parse_domains("\n".join(dict.fromkeys(out))), {"index": index_id, "suffix": suffix, "page": page}


def prioritize(domains: list[str]) -> list[str]:
    """Türkiye odağı: önce .tr, sonra Türkçe görünen adlar, en sonda geri kalanlar."""
    tr = [d for d in domains if d.endswith(".tr")]
    hint = [d for d in domains if not d.endswith(".tr") and looks_turkish_domain(d)]
    rest = [d for d in domains if not d.endswith(".tr") and not looks_turkish_domain(d)]
    return tr + hint + rest
