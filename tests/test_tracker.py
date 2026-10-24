import datetime as dt
from pathlib import Path

import pytest
import yaml

from ophtha_tracker.models import Deadline, Edition, Series, load_series
from ophtha_tracker.monitor import PageResult, compare, find_dates, html_to_text
from ophtha_tracker.report import build_report

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 24)


def make_series(**kwargs):
    defaults = dict(id="arvo", name="ARVO", scope="international", subspecialty="Ricerca",
                    website="https://arvo.example")
    return Series(**{**defaults, **kwargs})


def test_repository_data_is_valid():
    series = load_series(ROOT / "data" / "conferences.yaml")
    assert len({s.id for s in series}) == len(series)
    assert all(s.watch for s in series)


@pytest.mark.parametrize("override, error", [
    ({"scope": "mondiale"}, "scope"),
    ({"editions": [{"year": 2027, "start": "2027-05-06", "end": "2027-05-02"}]}, "precede"),
    ({"editions": [{"year": 2027, "deadlines": [{"type": "deadline", "date": "2026-12-01"}]}]},
     "tipo scadenza"),
    ({"editions": [{"year": 2027, "deadlines": [{"type": "abstract", "date": "2026-13-01"}]}]},
     "data non valida"),
])
def test_invalid_data_is_rejected(tmp_path, override, error):
    raw = {"id": "x", "name": "X", "scope": "international", "subspecialty": "Retina",
           "website": "https://x.example", **override}
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"series": [raw]}))
    with pytest.raises(ValueError, match=error):
        load_series(path)


def test_upcoming_edition_skips_past_ones():
    s = make_series(editions=[
        Edition(year=2026, start=dt.date(2026, 5, 3), end=dt.date(2026, 5, 7)),
        Edition(year=2027, start=dt.date(2027, 5, 2), end=dt.date(2027, 5, 6)),
    ])
    assert s.upcoming_edition(TODAY).year == 2027


def test_upcoming_edition_without_dates_uses_year():
    s = make_series(editions=[Edition(year=2025), Edition(year=2027)])
    assert s.upcoming_edition(TODAY).year == 2027


def test_find_dates_formats():
    text = "Deadline 1 March 2027; opens March 1, 2027; scadenza 15 gennaio 2027; 01/03/2027; 2027-03-01"
    assert find_dates(text) == ["1 March 2027", "March 1, 2027", "15 gennaio 2027",
                                "01/03/2027", "2027-03-01"]


def test_html_to_text_drops_scripts_and_navigation():
    html = "<html><nav>Menu</nav><script>x=1</script><p>Abstract   deadline</p></html>"
    assert html_to_text(html) == "Abstract deadline\n"


def test_compare_reports_only_new_dates():
    old = "Congress 2-6 May 2027\nAbstract deadline: 5 December 2026\n"
    new = "Congress 2-6 May 2027\nAbstract deadline extended to 12 December 2026\n"
    diff, dates = compare(old, new)
    assert any(line.startswith("+") for line in diff)
    assert dates == ["12 December 2026"]
    assert compare(None, new) == ([], [])
    assert compare(new, new) == ([], [])


def test_report_lists_deadlines_changes_errors_and_gaps():
    arvo = make_series(editions=[Edition(
        year=2027, city="San Diego", start=dt.date(2027, 5, 2), end=dt.date(2027, 5, 6),
        deadlines=[Deadline("abstract", dt.date(2026, 12, 6), verified=True),
                   Deadline("early_bird", dt.date(2027, 6, 1))],
    )])
    egs = make_series(id="egs", name="EGS", subspecialty="Glaucoma")
    pages = [
        PageResult("arvo", "https://arvo.example", diff=["-a", "+b"], new_dates=["6 December 2026"]),
        PageResult("egs", "https://egs.example", error="HTTP 403"),
    ]
    body = build_report([arvo, egs], pages, TODAY)

    assert "| **43** | [ARVO 2027](https://arvo.example) | Deadline abstract | 06/12/2026 | ✅ |" in body
    assert "01/06/2027" not in body.split("## 📅")[0]  # oltre l'orizzonte di 120 giorni
    assert "02/05/2027 – 06/05/2027 | San Diego" in body
    assert "Date nuove nel testo: 6 December 2026" in body
    assert "`HTTP 403`" in body
    assert "**EGS**: nessuna edizione futura inserita" in body
