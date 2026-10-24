"""Estrazione di date, sede e scadenze con Gemini, con verifica delle citazioni.

Ogni dato proposto dal modello deve essere accompagnato da una citazione
letterale del testo della pagina: se la citazione non compare nel testo
scaricato, o non contiene il giorno e il mese della data, il dato viene
scartato. I dati accettati restano comunque "da verificare".
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from .models import DEADLINE_TYPES, Deadline, Edition, Series
from .monitor import PageResult, describe_error, fetch_page

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-flash-latest"
SKIP_WITHIN_DAYS = 30      # congresso imminente: le deadline sono quasi certamente scadute
LOOKAHEAD_DAYS = 400       # oltre ~13 mesi le deadline di solito non sono ancora pubblicate
REQUIRED_DEADLINES = ("abstract", "early_bird")
MAX_EXTRA_PAGES = 3
MAX_PROMPT_CHARS = 80_000
CALL_INTERVAL = 5          # secondi tra una chiamata e l'altra (limiti del piano gratuito)
RETRY_STATUS = {429, 500, 503}
LABEL_DISTANCE = 300       # caratteri massimi tra etichetta e data nel testo

# Parole che l'etichetta (o la citazione) deve contenere per ciascun tipo di scadenza.
LABEL_KEYWORDS = {
    "abstract_open": r"abstract|free paper|e-?poster|comunicazion",
    "abstract": r"abstract|free paper|e-?poster|comunicazion",
    "late_breaking": r"late.?breaking",
    "early_bird": r"early|advance|reduced|ridott|anticipat",
    "registration": r"regist|iscrizion",
}

_EDITION_FIELDS = ("start", "end", "city", "country", "venue", "website")
_MONTH_NAMES = [
    ("january", "jan", "gennaio", "gen"), ("february", "feb", "febbraio"),
    ("march", "mar", "marzo"), ("april", "apr", "aprile"), ("may", "maggio", "mag"),
    ("june", "jun", "giugno", "giu"), ("july", "jul", "luglio", "lug"),
    ("august", "aug", "agosto", "ago"), ("september", "sep", "sept", "settembre", "set"),
    ("october", "oct", "ottobre", "ott"), ("november", "nov", "novembre"),
    ("december", "dec", "dicembre", "dic"),
]

_NULLABLE_STRING = {"type": "STRING", "nullable": True}
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "found": {"type": "BOOLEAN"},
        "year": {"type": "INTEGER", "nullable": True},
        "start": {**_NULLABLE_STRING, "description": "YYYY-MM-DD"},
        "end": {**_NULLABLE_STRING, "description": "YYYY-MM-DD"},
        "dates_quote": _NULLABLE_STRING,
        "city": _NULLABLE_STRING,
        "country": _NULLABLE_STRING,
        "venue": _NULLABLE_STRING,
        "location_quote": _NULLABLE_STRING,
        "website": _NULLABLE_STRING,
        "deadlines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "format": "enum", "enum": list(DEADLINE_TYPES)},
                    "date": {"type": "STRING", "description": "YYYY-MM-DD"},
                    "quote": {"type": "STRING"},
                    "label": {"type": "STRING"},
                },
                "required": ["type", "date", "quote", "label"],
            },
        },
    },
    "required": ["found", "deadlines"],
}

PROMPT = """\
Sei un assistente che estrae informazioni su congressi medici dal testo di pagine web.

Congresso: {name} ({society})
Data di oggi: {today}
Edizione cercata: {target}

Estrai SOLO informazioni presenti nel testo qui sotto, relative all'edizione cercata:
- start/end: date di inizio e fine del congresso (YYYY-MM-DD) e, in dates_quote,
  la frase del testo da cui le ricavi;
- city, country, venue: sede, con la frase di riferimento in location_quote;
- website: URL specifico dell'edizione, se presente nel testo;
- deadlines: scadenze con tipo tra
  abstract_open (apertura invio abstract), abstract (termine invio abstract),
  late_breaking (termine abstract late-breaking), early_bird (termine quota
  ridotta/early bird), registration (chiusura iscrizioni).
  Se una scadenza è stata prorogata, usa la data prorogata.

Regole obbligatorie:
- ogni "quote" deve essere copiata LETTERALMENTE dal testo (max 200 caratteri)
  e deve contenere la data a cui si riferisce;
- per ogni scadenza, "label" è la copia LETTERALE dell'etichetta o del titolo
  che dice di quale scadenza si tratta (es. "Abstract Submissions",
  "Early Bird Registration"); se etichetta e data sono nella stessa frase,
  ripeti la frase. Scadenze di corsi, wet lab, premi, borse o video NON sono
  scadenze abstract: escludile;
- se l'anno di una scadenza non è scritto, deducilo dal contesto dell'edizione;
- non inventare né stimare nulla: se un dato non c'è, usa null o ometti la scadenza;
- ignora le informazioni di edizioni passate;
- se il testo non riguarda l'edizione cercata, rispondi con found=false.

{pages}
"""


@dataclass
class Extraction:
    series_id: str
    checked: str
    model: str
    year: int | None = None
    start: str | None = None
    end: str | None = None
    city: str | None = None
    country: str | None = None
    venue: str | None = None
    website: str | None = None
    source: str | None = None
    deadlines: list[dict] = field(default_factory=list)  # {type, date, quote, source}
    discarded: list[str] = field(default_factory=list)


@dataclass
class SeriesOutcome:
    series_id: str
    action: str             # "extracted" | "not_found" | "skipped" | "error"
    reason: str
    changes: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ archivio estrazioni

def load_store(path: Path) -> dict[str, Extraction]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {sid: Extraction(**data) for sid, data in raw.items()}


def save_store(path: Path, store: dict[str, Extraction]) -> None:
    data = {sid: asdict(store[sid]) for sid in sorted(store)}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_store(series: list[Series], store: dict[str, Extraction]) -> None:
    """Integra i dati estratti in quelli manuali, che hanno sempre la precedenza."""
    for s in series:
        ext = store.get(s.id)
        if ext is None or ext.year is None:
            continue
        edition = next((e for e in s.editions if e.year == ext.year), None)
        if edition is None:
            edition = Edition(year=ext.year, origin="auto", source=ext.source)
            s.editions.append(edition)
        for name in _EDITION_FIELDS:
            value = getattr(ext, name)
            if value is not None and getattr(edition, name) is None:
                if name in ("start", "end"):
                    value = dt.date.fromisoformat(value)
                setattr(edition, name, value)
                edition.auto_fields.add(name)
        manual_types = {d.type for d in edition.deadlines}
        for d in ext.deadlines:
            if d["type"] not in manual_types:
                edition.deadlines.append(Deadline(
                    type=d["type"], date=dt.date.fromisoformat(d["date"]),
                    source=d["source"], origin="auto", quote=d["quote"],
                ))


# ------------------------------------------------------------------ quando cercare

def plan(series: Series, pages: list[PageResult], previous: Extraction | None,
         today: dt.date) -> str | None:
    """Motivo per cui NON cercare, oppure None se la ricerca va fatta."""
    if not any(p.text for p in pages):
        return "nessuna pagina raggiungibile"
    edition = series.upcoming_edition(today)
    if edition is not None and edition.start is not None:
        days = (edition.start - today).days
        if days < SKIP_WITHIN_DAYS:
            return f"congresso tra {days} giorni: deadline quasi certamente scadute"
        if days > LOOKAHEAD_DAYS:
            return f"congresso tra {days} giorni: deadline di solito non ancora pubblicate"
    complete = edition is not None and all(
        any(d.type == t for d in edition.deadlines) for t in REQUIRED_DEADLINES)
    if complete and previous is not None and not any(p.changed or p.is_new for p in pages):
        return "dati completi e pagine invariate"
    return None


# ------------------------------------------------------------------ verifica

def _norm(text: str) -> str:
    text = text.replace(" ", " ").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def find_quote(quote: str | None, pages: list[tuple[str, str]]) -> str | None:
    """URL della pagina che contiene la citazione, se c'è."""
    if not quote or len(quote.strip()) < 8:
        return None
    q = _norm(quote)
    return next((url for url, text in pages if q in _norm(text)), None)


def label_supports(kind: str, label: str | None, quote: str, source: str,
                   pages: list[tuple[str, str]]) -> bool:
    """L'etichetta è nel testo, vicina alla data, e coerente con il tipo di scadenza."""
    if not label or not re.search(LABEL_KEYWORDS[kind], _norm(f"{label} {quote}")):
        return False
    text = _norm(dict(pages)[source])
    q_pos = text.find(_norm(quote))
    lab = _norm(label)
    pos = text.find(lab)
    while pos != -1:
        if abs(pos - q_pos) <= LABEL_DISTANCE:
            return True
        pos = text.find(lab, pos + 1)
    return False


def date_in_quote(date: dt.date, quote: str) -> bool:
    q = _norm(quote)
    day = rf"(?<!\d)0?{date.day}(?!\d)"
    if not re.search(day, q):
        return False
    names = "|".join(_MONTH_NAMES[date.month - 1])
    return bool(
        re.search(rf"\b(?:{names})\b", q)
        or re.search(rf"(?<!\d)0?{date.day}[/.\-]0?{date.month}(?!\d)", q)
        or re.search(rf"(?<!\d)0?{date.month}[/.\-]0?{date.day}(?!\d)", q)
        or date.isoformat() in q
    )


def _parse_date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def validate(raw: dict, pages: list[tuple[str, str]], series_id: str, model: str,
             today: dt.date) -> Extraction:
    ext = Extraction(series_id=series_id, checked=today.isoformat(), model=model)
    year = raw.get("year")
    if not isinstance(year, int) or not today.year - 1 <= year <= today.year + 3:
        ext.discarded.append(f"anno non plausibile: {year!r}")
        return ext
    ext.year = year

    start, end = _parse_date(raw.get("start")), _parse_date(raw.get("end"))
    dates_src = find_quote(raw.get("dates_quote"), pages)
    if start or end:
        ok = dates_src and all(date_in_quote(d, raw["dates_quote"]) for d in (start, end) if d)
        if ok and not (start and end and end < start):
            ext.start = start.isoformat() if start else None
            ext.end = end.isoformat() if end else None
            ext.source = dates_src
        else:
            ext.discarded.append(f"date del congresso senza riscontro nel testo: {raw.get('dates_quote')!r}")

    location_src = find_quote(raw.get("location_quote"), pages)
    if any(raw.get(k) for k in ("city", "venue")):
        quote = _norm(raw.get("location_quote") or "")
        if location_src and all(_norm(raw[k]) in quote for k in ("city", "venue") if raw.get(k)):
            ext.city, ext.country, ext.venue = raw.get("city"), raw.get("country"), raw.get("venue")
            ext.source = ext.source or location_src
        else:
            ext.discarded.append(f"sede senza riscontro nel testo: {raw.get('location_quote')!r}")

    website = raw.get("website")
    if website and any(website == url or website in text for url, text in pages):
        ext.website = website

    seen: set[str] = set()
    last_day = _parse_date(ext.end) or _parse_date(ext.start)
    for d in raw.get("deadlines") or []:
        date, kind, quote = _parse_date(d.get("date")), d.get("type"), d.get("quote", "")
        label = f"{kind} {d.get('date')}"
        source = find_quote(quote, pages)
        if kind not in DEADLINE_TYPES or date is None:
            ext.discarded.append(f"{label}: tipo o data non validi")
        elif kind in seen:
            ext.discarded.append(f"{label}: duplicato")
        elif source is None:
            ext.discarded.append(f"{label}: citazione assente dal testo")
        elif not date_in_quote(date, quote):
            ext.discarded.append(f"{label}: la citazione non contiene la data")
        elif not label_supports(kind, d.get("label"), quote, source, pages):
            ext.discarded.append(f"{label}: etichetta {d.get('label')!r} assente o non coerente "
                                 f"con il tipo di scadenza")
        elif last_day and date > last_day:
            ext.discarded.append(f"{label}: successiva alla fine del congresso")
        else:
            seen.add(kind)
            ext.deadlines.append({"type": kind, "date": date.isoformat(), "quote": quote.strip(),
                                  "label": d.get("label"), "source": source})
    ext.deadlines.sort(key=lambda d: d["date"])
    return ext


def diff_extractions(old: Extraction | None, new: Extraction) -> list[str]:
    before = {d["type"]: d["date"] for d in old.deadlines} if old and old.year == new.year else {}
    changes = []
    for d in new.deadlines:
        label = DEADLINE_TYPES[d["type"]]
        if d["type"] not in before:
            changes.append(f"{label}: {d['date']} (nuova)")
        elif before[d["type"]] != d["date"]:
            changes.append(f"{label}: {before[d['type']]} → {d['date']} (cambiata)")
    if new.start and (old is None or old.year != new.year or old.start != new.start):
        changes.append(f"date congresso: {new.start} – {new.end or '?'}")
    if new.city and (old is None or old.year != new.year or old.city != new.city):
        changes.append(f"sede: {', '.join(p for p in (new.venue, new.city, new.country) if p)}")
    return changes


# ------------------------------------------------------------------ Gemini

def call_gemini(prompt: str, api_key: str, model: str, session: requests.Session) -> dict:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    for attempt in range(3):
        resp = session.post(GEMINI_URL.format(model=model), json=body, timeout=120,
                            headers={"x-goog-api-key": api_key})
        if resp.status_code not in RETRY_STATUS or attempt == 2:
            break
        time.sleep(15 * (attempt + 1))
    resp.raise_for_status()
    candidates = resp.json().get("candidates") or []
    if not candidates:
        raise ValueError("risposta Gemini vuota (contenuto bloccato o quota esaurita)")
    parts = candidates[0].get("content", {}).get("parts") or []
    return json.loads("".join(p.get("text", "") for p in parts))


def _target(series: Series, today: dt.date) -> str:
    edition = series.upcoming_edition(today)
    if edition is None:
        return f"la prossima edizione che inizia dopo il {today.isoformat()}"
    return f"edizione {edition.year}"


def _collect_pages(pages: list[PageResult], session: requests.Session) -> list[tuple[str, str]]:
    collected = [(p.url, p.text) for p in pages if p.text]
    known = {url for url, _ in collected}
    extra = [link for p in pages for link in p.links if link not in known]
    for url in list(dict.fromkeys(extra))[:MAX_EXTRA_PAGES]:
        try:
            collected.append((url, fetch_page(url, session).text))
        except Exception:  # noqa: BLE001 - le sottopagine sono facoltative
            continue
    return collected


def _render_pages(pages: list[tuple[str, str]]) -> str:
    budget = MAX_PROMPT_CHARS // max(len(pages), 1)
    return "\n\n".join(f"=== PAGINA: {url} ===\n{text[:budget]}" for url, text in pages)


def run(series: list[Series], results: list[PageResult], store: dict[str, Extraction],
        api_key: str, model: str, today: dt.date) -> list[SeriesOutcome]:
    """Aggiorna `store` sul posto e restituisce l'esito per ogni congresso."""
    outcomes = []
    first_call = True
    with requests.Session() as session:
        for s in series:
            pages = [p for p in results if p.series_id == s.id]
            reason = plan(s, pages, store.get(s.id), today)
            if reason:
                outcomes.append(SeriesOutcome(s.id, "skipped", reason))
                continue
            if not first_call:
                time.sleep(CALL_INTERVAL)
            first_call = False

            collected = _collect_pages(pages, session)
            prompt = PROMPT.format(name=s.name, society=s.society or s.name, today=today.isoformat(),
                                   target=_target(s, today), pages=_render_pages(collected))
            try:
                raw = call_gemini(prompt, api_key, model, session)
            except Exception as exc:  # noqa: BLE001 - l'errore va nel report
                outcomes.append(SeriesOutcome(s.id, "error", f"Gemini: {describe_error(exc)}"))
                continue

            if not raw.get("found"):
                outcomes.append(SeriesOutcome(s.id, "not_found",
                                              f"nessuna informazione nelle {len(collected)} pagine lette"))
                continue
            ext = validate(raw, collected, s.id, model, today)
            if ext.year is None:
                outcomes.append(SeriesOutcome(s.id, "not_found", "edizione non identificata",
                                              discarded=ext.discarded))
                continue
            changes = diff_extractions(store.get(s.id), ext)
            store[s.id] = ext
            outcomes.append(SeriesOutcome(
                s.id, "extracted", f"{len(collected)} pagine lette, edizione {ext.year}",
                changes=changes, discarded=ext.discarded))
    return outcomes
