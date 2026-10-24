import datetime as dt

import pytest

from ophtha_tracker import extractor
from ophtha_tracker.extractor import (Extraction, apply_store, date_in_quote, diff_extractions,
                                      load_store, plan, save_store, validate)
from ophtha_tracker.models import Deadline, Edition, Series
from ophtha_tracker.monitor import PageResult
from ophtha_tracker.report import build_report

TODAY = dt.date(2026, 10, 24)
PAGE = ("https://arvo.example/abstracts", """\
ARVO 2027 Annual Meeting
May 2-6, 2027, San Diego Convention Center, San Diego
Abstract submission opens October 15, 2026.
Abstract deadline: Sunday, December 6, 2026 at 11:59 pm EST.
Early registration ends 15/03/2027.
""")


def make_series(**kwargs):
    defaults = dict(id="arvo", name="ARVO", scope="international", subspecialty="Ricerca",
                    website="https://arvo.example", watch=["https://arvo.example"])
    return Series(**{**defaults, **kwargs})


def page(changed=False, text="testo", error=None):
    return PageResult("arvo", "https://arvo.example", diff=["+x"] if changed else [],
                      text=None if error else text, error=error)


GOOD = {
    "found": True, "year": 2027, "start": "2027-05-02", "end": "2027-05-06",
    "dates_quote": "May 2-6, 2027", "city": "San Diego", "country": "USA",
    "venue": "San Diego Convention Center",
    "location_quote": "San Diego Convention Center, San Diego",
    "deadlines": [
        {"type": "abstract", "date": "2026-12-06",
         "quote": "Abstract deadline: Sunday, December 6, 2026 at 11:59 pm EST."},
        {"type": "early_bird", "date": "2027-03-15", "quote": "Early registration ends 15/03/2027"},
    ],
}


@pytest.mark.parametrize("date, quote, expected", [
    (dt.date(2026, 12, 6), "Abstract deadline: December 6, 2026", True),
    (dt.date(2026, 12, 6), "scadenza abstract 6 dicembre", True),
    (dt.date(2027, 3, 15), "ends 15/03/2027", True),
    (dt.date(2026, 12, 7), "Abstract deadline: December 6, 2026", False),
    (dt.date(2026, 11, 6), "Abstract deadline: December 6, 2026", False),
])
def test_date_in_quote(date, quote, expected):
    assert date_in_quote(date, quote) is expected


def test_validate_accepts_supported_data():
    ext = validate(GOOD, [PAGE], "arvo", "m", TODAY)
    assert (ext.year, ext.start, ext.end, ext.city) == (2027, "2027-05-02", "2027-05-06", "San Diego")
    assert [d["type"] for d in ext.deadlines] == ["abstract", "early_bird"]
    assert all(d["source"] == PAGE[0] for d in ext.deadlines)
    assert ext.discarded == []


def test_validate_discards_hallucinations():
    raw = {**GOOD, "city": "Boston", "deadlines": [
        {"type": "abstract", "date": "2026-12-01", "quote": "Abstract deadline: December 1, 2026"},
        {"type": "abstract_open", "date": "2026-10-20",
         "quote": "Abstract submission opens October 15, 2026."},
        {"type": "registration", "date": "2027-06-01", "quote": "Early registration ends 15/03/2027"},
    ]}
    ext = validate(raw, [PAGE], "arvo", "m", TODAY)
    assert ext.deadlines == [] and ext.city is None
    reasons = " ".join(ext.discarded)
    assert "citazione assente" in reasons
    assert "non contiene la data" in reasons
    assert "sede senza riscontro" in reasons


def test_validate_rejects_implausible_year():
    ext = validate({**GOOD, "year": 2019}, [PAGE], "arvo", "m", TODAY)
    assert ext.year is None


def test_plan_respects_time_window():
    soon = make_series(editions=[Edition(year=2026, start=TODAY + dt.timedelta(days=20))])
    far = make_series(editions=[Edition(year=2028, start=TODAY + dt.timedelta(days=500))])
    window = make_series(editions=[Edition(year=2027, start=TODAY + dt.timedelta(days=190))])
    unknown = make_series()
    assert "deadline quasi certamente scadute" in plan(soon, [page()], None, TODAY)
    assert "non ancora pubblicate" in plan(far, [page()], None, TODAY)
    assert plan(window, [page()], None, TODAY) is None
    assert plan(unknown, [page()], None, TODAY) is None
    assert plan(window, [page(error="HTTP 403")], None, TODAY) == "nessuna pagina raggiungibile"


def test_plan_skips_complete_unchanged_series():
    complete = make_series(editions=[Edition(
        year=2027, start=TODAY + dt.timedelta(days=190),
        deadlines=[Deadline("abstract", TODAY), Deadline("early_bird", TODAY)])])
    previous = Extraction("arvo", "2026-10-01", "m", year=2027)
    assert plan(complete, [page()], previous, TODAY) == "dati completi e pagine invariate"
    assert plan(complete, [page(changed=True)], previous, TODAY) is None


def test_manual_data_wins_over_extraction(tmp_path):
    ext = validate(GOOD, [PAGE], "arvo", "m", TODAY)
    path = tmp_path / "extracted.json"
    save_store(path, {"arvo": ext})
    store = load_store(path)

    s = make_series(editions=[Edition(year=2027, city="Sede manuale", verified=True,
                                      deadlines=[Deadline("abstract", dt.date(2026, 12, 1), True)])])
    apply_store([s], store)
    e = s.editions[0]
    assert e.city == "Sede manuale" and e.start == dt.date(2027, 5, 2)
    assert e.auto_fields == {"start", "end", "country", "venue"}
    by_type = {d.type: d for d in e.deadlines}
    assert by_type["abstract"].date == dt.date(2026, 12, 1) and by_type["abstract"].origin == "manual"
    assert by_type["early_bird"].origin == "auto"


def test_diff_extractions_reports_new_and_changed_deadlines():
    old = validate(GOOD, [PAGE], "arvo", "m", TODAY)
    new = Extraction("arvo", "2026-11-01", "m", year=2027, start=old.start, end=old.end,
                     deadlines=[{**old.deadlines[0], "date": "2026-12-13"},
                                {"type": "late_breaking", "date": "2027-02-01", "quote": "", "source": ""}])
    changes = diff_extractions(old, new)
    assert "Deadline abstract: 2026-12-06 → 2026-12-13 (cambiata)" in changes
    assert "Late-breaking abstract: 2027-02-01 (nuova)" in changes


def test_run_end_to_end_with_mocked_gemini(monkeypatch):
    calls = []

    def fake_call(prompt, api_key, model, session):
        calls.append(prompt)
        return GOOD

    monkeypatch.setattr(extractor, "call_gemini", fake_call)
    monkeypatch.setattr(extractor, "CALL_INTERVAL", 0)
    s = make_series()
    results = [PageResult("arvo", PAGE[0], text=PAGE[1], is_new=True)]
    store = {}
    outcomes = extractor.run([s], results, store, "key", "m", TODAY)

    assert len(calls) == 1 and "ARVO 2027 Annual Meeting" in calls[0]
    assert outcomes[0].action == "extracted"
    assert "Deadline abstract: 2026-12-06 (nuova)" in outcomes[0].changes

    apply_store([s], store)
    body = build_report([s], results, TODAY, outcomes=outcomes)
    assert "| 06/12/2026 | 🤖 estratto, da verificare |" in body
    assert "🆕 Deadline abstract: 2026-12-06 (nuova)" in body


def test_run_reports_gemini_errors(monkeypatch):
    def failing(*args):
        raise ValueError("risposta Gemini vuota")

    monkeypatch.setattr(extractor, "call_gemini", failing)
    results = [PageResult("arvo", PAGE[0], text=PAGE[1])]
    outcomes = extractor.run([make_series()], results, {}, "key", "m", TODAY)
    assert outcomes[0].action == "error" and "vuota" in outcomes[0].reason


def test_call_gemini_request_and_response_parsing():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"found": false, '},
                                                          {"text": '"deadlines": []}'}]}}]}

    class FakeSession:
        def post(self, url, json, timeout, headers):
            self.url, self.body, self.headers = url, json, headers
            return FakeResponse()

    session = FakeSession()
    assert extractor.call_gemini("prompt", "k", "gemini-x", session) == {"found": False, "deadlines": []}
    assert session.url.endswith("/models/gemini-x:generateContent")
    assert session.headers == {"x-goog-api-key": "k"}
    config = session.body["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["temperature"] == 0
