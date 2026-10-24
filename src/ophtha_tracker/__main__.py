"""Uso: python -m ophtha_tracker [--data ...] [--snapshots ...] [--report ...]"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from . import extractor
from .models import load_series
from .monitor import check_all
from .report import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitoraggio deadline congressi oftalmologici")
    parser.add_argument("--data", type=Path, default=Path("data/conferences.yaml"))
    parser.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--report", type=Path, default=Path("report.md"))
    parser.add_argument("--extracted", type=Path, default=Path("data/extracted.json"),
                        help="archivio dei dati estratti da Gemini")
    parser.add_argument("--no-llm", action="store_true", help="non usare Gemini")
    parser.add_argument("--horizon", type=int, default=120, help="giorni di anticipo per le scadenze")
    parser.add_argument("--validate-only", action="store_true", help="valida solo il file dati")
    args = parser.parse_args(argv)

    series = load_series(args.data)
    print(f"{len(series)} congressi caricati da {args.data}")
    if args.validate_only:
        return 0

    targets = [(s.id, url) for s in series for url in s.watch]
    pages = check_all(targets, args.snapshots)
    for p in pages:
        status = p.error or ("nuova" if p.is_new else "modificata" if p.changed else "invariata")
        print(f"  [{p.series_id}] {p.url}: {status}")

    today = dt.date.today()
    store = extractor.load_store(args.extracted)
    extractor.apply_store(series, store)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    outcomes = None
    if api_key and not args.no_llm:
        model = os.environ.get("GEMINI_MODEL", "").strip() or extractor.DEFAULT_MODEL
        outcomes = extractor.run(series, pages, store, api_key, model, today)
        for o in outcomes:
            print(f"  [{o.series_id}] Gemini {o.action}: {o.reason}")
        extractor.save_store(args.extracted, store)
        # Ricarica i dati manuali e reintegra le estrazioni aggiornate.
        series = load_series(args.data)
        extractor.apply_store(series, store)
    else:
        print("Ricerca Gemini saltata (GEMINI_API_KEY assente o --no-llm)")

    args.report.write_text(build_report(series, pages, today, args.horizon, outcomes), encoding="utf-8")
    print(f"Report scritto in {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
