"""Uso: python -m ophtha_tracker [--data ...] [--snapshots ...] [--report ...]"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from .models import load_series
from .monitor import check_all
from .report import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitoraggio deadline congressi oftalmologici")
    parser.add_argument("--data", type=Path, default=Path("data/conferences.yaml"))
    parser.add_argument("--snapshots", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--report", type=Path, default=Path("report.md"))
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

    args.report.write_text(build_report(series, pages, dt.date.today(), args.horizon), encoding="utf-8")
    print(f"Report scritto in {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
