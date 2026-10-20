"""Yeni alan adı kaynakları.

Ücretsiz kaynaklar:
  * WhoisDS günlük "newly registered domains" listesi (zip içinde txt)
  * Elle hazırlanmış bir domains.txt dosyası (Certstream/crt.sh çıktısı vb.)
"""
import base64
import datetime as dt
import io
import re
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


def prioritize(domains: list[str]) -> list[str]:
    """Türkiye odağı: .tr uzantılı alan adları ve .com.tr önce taranır."""
    tr = [d for d in domains if d.endswith(".tr")]
    rest = [d for d in domains if not d.endswith(".tr")]
    return tr + rest
