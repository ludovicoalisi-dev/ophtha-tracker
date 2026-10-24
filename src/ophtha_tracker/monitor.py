"""Scarica le pagine monitorate e le confronta con l'ultimo snapshot."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class PageResult:
    series_id: str
    url: str
    error: str | None = None
    is_new: bool = False
    diff: list[str] = field(default_factory=list)
    new_dates: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.diff)


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


def fetch_text(url: str, session: requests.Session) -> str:
    resp = session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        raise ValueError(f"contenuto non testuale ({content_type or 'sconosciuto'})")
    return html_to_text(resp.text)


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
        text = fetch_text(url, session)
    except requests.HTTPError as exc:
        result.error = f"HTTP {exc.response.status_code}"
        return result
    except (requests.RequestException, ValueError) as exc:
        result.error = type(exc).__name__ if isinstance(exc, requests.RequestException) else str(exc)
        return result

    path = snapshot_path(snapshots, series_id, url)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    result.is_new = old is None
    result.diff, result.new_dates = compare(old, text)
    path.write_text(text, encoding="utf-8")
    return result


def check_all(targets: list[tuple[str, str]], snapshots: Path) -> list[PageResult]:
    snapshots.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        return [check_page(sid, url, snapshots, session) for sid, url in targets]
