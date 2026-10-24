"""Scarica le pagine monitorate e le confronta con l'ultimo snapshot."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 30
MAX_DIFF_LINES = 60

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
    "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)
DATE_RE = re.compile(
    rf"\b(?:"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?,?\s+\d{{4}}"      # 1 March 2027
    rf"|(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"     # March 1, 2027
    rf"|\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{4}}"                             # 01/03/2027
    rf"|\d{{4}}-\d{{2}}-\d{{2}}"                                       # 2027-03-01
    rf")\b",
    re.IGNORECASE,
)


LINK_RE = re.compile(
    r"abstract|submission|call.for|deadline|important.dates|key.dates|registration|register"
    r"|iscrizion|scadenz|date.importanti|comunicazion",
    re.IGNORECASE,
)


@dataclass
class Page:
    text: str
    links: list[str] = field(default_factory=list)


@dataclass
class PageResult:
    series_id: str
    url: str
    error: str | None = None
    is_new: bool = False
    diff: list[str] = field(default_factory=list)
    new_dates: list[str] = field(default_factory=list)
    text: str | None = None
    links: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.diff)


def _same_site(a: str, b: str) -> bool:
    def root(url: str) -> str:
        return ".".join((urlparse(url).hostname or "").split(".")[-2:])
    return root(a) == root(b)


def relevant_links(html: str, base_url: str) -> list[str]:
    """Link interni che probabilmente portano a pagine su abstract o iscrizioni."""
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"]).split("#")[0]
        if (url.startswith("http") and url != base_url and _same_site(url, base_url)
                and (LINK_RE.search(a.get_text(" ")) or LINK_RE.search(urlparse(url).path))):
            links.append(url)
    return list(dict.fromkeys(links))


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    lines = (re.sub(r"\s+", " ", line).strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line) + "\n"


def find_dates(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in DATE_RE.finditer(text)))


def snapshot_path(directory: Path, series_id: str, url: str) -> Path:
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    return directory / f"{series_id}--{digest}.txt"


def fetch_page(url: str, session: requests.Session) -> Page:
    resp = session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        raise ValueError(f"contenuto non testuale ({content_type or 'sconosciuto'})")
    return Page(text=html_to_text(resp.text), links=relevant_links(resp.text, resp.url))


def compare(old: str | None, new: str) -> tuple[list[str], list[str]]:
    """Restituisce (righe di diff, date comparse nelle righe aggiunte)."""
    if old is None or old == new:
        return [], []
    diff = [
        line for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1)
        if not line.startswith(("---", "+++"))
    ]
    added = "\n".join(line[1:] for line in diff if line.startswith("+"))
    old_dates = set(find_dates(old))
    new_dates = [d for d in find_dates(added) if d not in old_dates]
    return diff, new_dates


def check_page(series_id: str, url: str, snapshots: Path, session: requests.Session) -> PageResult:
    result = PageResult(series_id=series_id, url=url)
    try:
        page = fetch_page(url, session)
    except Exception as exc:  # noqa: BLE001 - qualsiasi errore va solo riportato
        result.error = describe_error(exc)
        return result

    path = snapshot_path(snapshots, series_id, url)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    result.is_new = old is None
    result.diff, result.new_dates = compare(old, page.text)
    result.text, result.links = page.text, page.links
    path.write_text(page.text, encoding="utf-8")
    return result


def describe_error(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.RequestException):
        return type(exc).__name__
    return str(exc)


def check_all(targets: list[tuple[str, str]], snapshots: Path) -> list[PageResult]:
    snapshots.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        return [check_page(sid, url, snapshots, session) for sid, url in targets]
