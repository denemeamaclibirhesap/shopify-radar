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


def prioritize(domains: list[str]) -> list[str]:
    """Türkiye odağı: önce .tr, sonra Türkçe görünen adlar, en sonda geri kalanlar."""
    tr = [d for d in domains if d.endswith(".tr")]
    hint = [d for d in domains if not d.endswith(".tr") and looks_turkish_domain(d)]
    rest = [d for d in domains if not d.endswith(".tr") and not looks_turkish_domain(d)]
    return tr + hint + rest
