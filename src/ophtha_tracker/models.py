"""Caricamento e validazione di data/conferences.yaml."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEADLINE_TYPES = {
    "abstract_open": "Apertura abstract",
    "abstract": "Deadline abstract",
    "late_breaking": "Late-breaking abstract",
    "early_bird": "Early bird",
    "registration": "Chiusura iscrizioni",
}
SCOPES = {"international": "Internazionale", "national": "Nazionale"}


@dataclass
class Deadline:
    type: str
    date: dt.date
    verified: bool = False
    source: str | None = None

    @property
    def label(self) -> str:
        return DEADLINE_TYPES[self.type]


@dataclass
class Edition:
    year: int
    city: str | None = None
    country: str | None = None
    venue: str | None = None
    start: dt.date | None = None
    end: dt.date | None = None
    website: str | None = None
    verified: bool = False
    source: str | None = None
    notes: str | None = None
    deadlines: list[Deadline] = field(default_factory=list)

    @property
    def location(self) -> str:
        parts = [p for p in (self.venue, self.city, self.country) if p]
        return ", ".join(parts) if parts else "n.d."


@dataclass
class Series:
    id: str
    name: str
    scope: str
    subspecialty: str
    website: str
    society: str | None = None
    notes: str | None = None
    watch: list[str] = field(default_factory=list)
    editions: list[Edition] = field(default_factory=list)

    def upcoming_edition(self, today: dt.date) -> Edition | None:
        """Prima edizione non ancora conclusa (o senza date note)."""
        def pending(e: Edition) -> bool:
            last_day = e.end or e.start
            return last_day >= today if last_day else e.year >= today.year

        candidates = [e for e in self.editions if pending(e)]
        return min(candidates, key=lambda e: (e.year, e.start or dt.date.max), default=None)


def _date(value, where: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{where}: data non valida {value!r} (atteso YYYY-MM-DD)") from None


def _deadline(raw: dict, where: str) -> Deadline:
    kind = raw.get("type")
    if kind not in DEADLINE_TYPES:
        raise ValueError(f"{where}: tipo scadenza {kind!r} non ammesso ({', '.join(DEADLINE_TYPES)})")
    date = _date(raw.get("date"), where)
    if date is None:
        raise ValueError(f"{where}: scadenza {kind!r} senza data")
    return Deadline(type=kind, date=date, verified=bool(raw.get("verified", False)),
                    source=raw.get("source"))


def _edition(raw: dict, where: str) -> Edition:
    where = f"{where} {raw.get('year')}"
    if not isinstance(raw.get("year"), int):
        raise ValueError(f"{where}: campo 'year' mancante o non intero")
    edition = Edition(
        year=raw["year"],
        city=raw.get("city"),
        country=raw.get("country"),
        venue=raw.get("venue"),
        start=_date(raw.get("start"), where),
        end=_date(raw.get("end"), where),
        website=raw.get("website"),
        verified=bool(raw.get("verified", False)),
        source=raw.get("source"),
        notes=raw.get("notes"),
        deadlines=[_deadline(d, where) for d in raw.get("deadlines") or []],
    )
    if edition.start and edition.end and edition.end < edition.start:
        raise ValueError(f"{where}: 'end' precede 'start'")
    return edition


def load_series(path: Path) -> list[Series]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    series: list[Series] = []
    seen: set[str] = set()
    for raw in data.get("series") or []:
        sid = raw.get("id")
        if not sid or sid in seen:
            raise ValueError(f"id serie mancante o duplicato: {sid!r}")
        seen.add(sid)
        for key in ("name", "scope", "subspecialty", "website"):
            if not raw.get(key):
                raise ValueError(f"{sid}: campo obbligatorio '{key}' mancante")
        if raw["scope"] not in SCOPES:
            raise ValueError(f"{sid}: scope {raw['scope']!r} non ammesso ({', '.join(SCOPES)})")
        series.append(Series(
            id=sid,
            name=raw["name"],
            scope=raw["scope"],
            subspecialty=raw["subspecialty"],
            website=raw["website"],
            society=raw.get("society"),
            notes=raw.get("notes"),
            watch=list(raw.get("watch") or []),
            editions=[_edition(e, sid) for e in raw.get("editions") or []],
        ))
    return series
