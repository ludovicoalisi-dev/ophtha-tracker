"""Generazione del report Markdown pubblicato come issue GitHub."""

from __future__ import annotations

import datetime as dt

from .models import DEADLINE_TYPES, SCOPES, Series
from .monitor import MAX_DIFF_LINES, PageResult

REQUIRED_DEADLINES = ("abstract", "early_bird")
MAX_BODY_CHARS = 60_000  # limite issue GitHub: 65 536


def _fmt(d: dt.date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "n.d."


def _dates(start: dt.date | None, end: dt.date | None) -> str:
    if start and end and start != end:
        return f"{_fmt(start)} – {_fmt(end)}"
    return _fmt(start or end)


def _check(verified: bool) -> str:
    return "✅" if verified else "⚠️ da verificare"


def _upcoming_deadlines(series: list[Series], today: dt.date, horizon: int) -> list[str]:
    rows = []
    for s in series:
        for e in s.editions:
            for d in e.deadlines:
                days = (d.date - today).days
                if 0 <= days <= horizon:
                    link = d.source or e.website or s.website
                    rows.append((days, f"| **{days}** | [{s.name} {e.year}]({link}) | {d.label} "
                                       f"| {_fmt(d.date)} | {_check(d.verified)} |"))
    if not rows:
        return [f"_Nessuna scadenza registrata nei prossimi {horizon} giorni._"]
    header = ["| Giorni | Congresso | Scadenza | Data | Stato |", "|---:|---|---|---|---|"]
    return header + [row for _, row in sorted(rows)]


def _editions_table(series: list[Series], today: dt.date) -> list[str]:
    rows = []
    for s in series:
        e = s.upcoming_edition(today)
        if e is None:
            continue
        rows.append((e.start or dt.date.max, f"| [{s.name} {e.year}]({e.website or s.website}) "
                                             f"| {SCOPES[s.scope]} | {s.subspecialty} "
                                             f"| {_dates(e.start, e.end)} | {e.location} "
                                             f"| {_check(e.verified)} |"))
    if not rows:
        return ["_Nessuna edizione futura registrata._"]
    header = ["| Congresso | Ambito | Sottospecialità | Date | Sede | Stato |",
              "|---|---|---|---|---|---|"]
    return header + [row for _, row in sorted(rows)]


def _changes(pages: list[PageResult], names: dict[str, str]) -> list[str]:
    changed = [p for p in pages if p.changed]
    if not changed:
        return ["_Nessuna pagina modificata._"]
    out = []
    for p in changed:
        line = f"- **{names.get(p.series_id, p.series_id)}** — <{p.url}>"
        if p.new_dates:
            line += f"  \n  📌 Date nuove nel testo: {', '.join(p.new_dates)}"
        diff = p.diff[:MAX_DIFF_LINES]
        if len(p.diff) > MAX_DIFF_LINES:
            diff.append(f"... ({len(p.diff) - MAX_DIFF_LINES} righe omesse)")
        out += [line, "  <details><summary>Differenze</summary>", "", "  ```diff",
                *(f"  {d}" for d in diff), "  ```", "  </details>"]
    return out


def _errors(pages: list[PageResult], names: dict[str, str]) -> list[str]:
    failed = [p for p in pages if p.error]
    if not failed:
        return ["_Tutte le pagine sono state scaricate correttamente._"]
    return [f"- **{names.get(p.series_id, p.series_id)}** — <{p.url}> — `{p.error}`" for p in failed]


def _missing(series: list[Series], today: dt.date) -> list[str]:
    out = []
    for s in series:
        e = s.upcoming_edition(today)
        if e is None:
            out.append(f"- **{s.name}**: nessuna edizione futura inserita")
            continue
        gaps = [label for label, ok in (
            ("date", e.start is not None),
            ("sede", e.city is not None),
            *((DEADLINE_TYPES[t].lower(), any(d.type == t for d in e.deadlines))
              for t in REQUIRED_DEADLINES),
        ) if not ok]
        if gaps:
            out.append(f"- **{s.name} {e.year}**: manca {', '.join(gaps)}")
    return out or ["_Tutti i dati essenziali sono presenti._"]


def build_report(series: list[Series], pages: list[PageResult], today: dt.date,
                 horizon: int = 120) -> str:
    names = {s.id: s.name for s in series}
    baseline = [p for p in pages if p.is_new and not p.error]
    lines = [
        f"Controllo del {_fmt(today)}. Le date segnate ⚠️ non sono state confermate "
        "sul sito ufficiale: verificale prima di pianificare.",
        "",
        f"## ⏰ Scadenze nei prossimi {horizon} giorni", "", *_upcoming_deadlines(series, today, horizon),
        "", "## 📅 Prossime edizioni", "", *_editions_table(series, today),
        "", "## 🔄 Pagine modificate dall'ultimo controllo", "", *_changes(pages, names),
    ]
    if baseline:
        lines += ["", f"_Prima acquisizione per {len(baseline)} pagine: il confronto partirà dal prossimo controllo._"]
    lines += [
        "", "## 🚫 Pagine non raggiungibili", "", *_errors(pages, names),
        "", "## 📝 Dati da completare in `data/conferences.yaml`", "", *_missing(series, today),
        "",
    ]
    body = "\n".join(lines)
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n_… report troncato per limiti di lunghezza dell'issue._\n"
    return body
