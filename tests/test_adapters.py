from datetime import date

from activity_radar.adapters.base import AdapterWindow, RawCandidate
from activity_radar.adapters.calendar_seed import CalendarSeedAdapter
from activity_radar.adapters.onepilot import map_onepilot_row
from activity_radar.adapters.generic import HtmlListAdapter, extract_html_candidates, extract_jsonld_candidates, extract_json_rows


def test_onepilot_row_maps_to_raw_candidate_and_truncates_excerpt():
    row = {
        "external_id": "google-devfest-2026",
        "title": "2026 Google Devfest 谷歌开发者节",
        "date": "2026-11-07",
        "start_date": "2026-11-07",
        "end_date": "2026-11-07",
        "location": "上海",
        "district": "浦东新区",
        "organizer": "GDG Shanghai",
        "event_type": "开发者大会",
        "summary": "A" * 700,
    }

    candidate = map_onepilot_row(row, "2026-08-18T00:00:00+00:00")

    assert isinstance(candidate, RawCandidate)
    assert candidate.source_id == "onepilot"
    assert candidate.raw_title == row["title"]
    assert candidate.date_start == "2026-11-07"
    assert candidate.city == "上海"
    assert candidate.venue == "上海"
    assert candidate.organizer == "GDG Shanghai"
    assert candidate.url.endswith("/events/google-devfest-2026/register")
    assert len(candidate.raw_excerpt) == 600


def test_calendar_seed_emits_confirmed_and_expected_rows(tmp_path):
    calendar = tmp_path / "annual_calendar.yaml"
    calendar.write_text(
        """
events:
  - id: confirmed
    name: Confirmed Conference
    typical_month: 9
    city: 杭州
    official_url: https://example.com/confirmed
    why_it_matters: P0
    last_known_dates: 2026-09-22/2026-09-24
    confirmed_dates:
      start: 2026-09-22
      end: 2026-09-24
  - id: expected
    name: Expected Conference
    typical_month: 10
    city: 上海
    official_url: https://example.com/expected
    why_it_matters: P0
    last_known_dates: 2025-10-15/2025-10-17
""".strip(),
        encoding="utf-8",
    )
    adapter = CalendarSeedAdapter()
    rows = adapter.fetch(
        {"id": "calendar-seed", "params": {"path": str(calendar)}},
        AdapterWindow(start=date(2026, 8, 18), end=date(2026, 12, 16)),
    )

    assert [(row.raw_title, row.date_start, row.date_end, row.status, row.date_precision) for row in rows] == [
        ("Confirmed Conference", "2026-09-22", "2026-09-24", "active", "day"),
        ("Expected Conference", "2026-10-01", "2026-10-01", "expected", "month"),
    ]


def test_jsonld_event_extraction_handles_graph_and_location():
    html = """
    <script type="application/ld+json">{"@graph":[{"@type":"Event","name":"AI Summit","startDate":"2026-09-10T09:00:00+08:00","endDate":"2026-09-11","url":"/summit","location":{"name":"Expo","address":{"addressLocality":"上海"}},"organizer":{"name":"Platform"},"description":"Official event"}]}</script>
    """
    rows = extract_jsonld_candidates(html, {"id": "official", "url": "https://example.com/events"}, "now")
    assert len(rows) == 1
    assert rows[0].raw_title == "AI Summit"
    assert rows[0].date_start == "2026-09-10"
    assert rows[0].date_end == "2026-09-11"
    assert rows[0].city == "上海"
    assert rows[0].url == "https://example.com/summit"


def test_html_list_extraction_uses_configured_selectors():
    html = """
    <div class="event"><a class="title" href="/a">跨境增长沙龙</a><span class="date">2026.09.20</span><span class="place">杭州 国际中心</span><p>品牌出海负责人交流</p></div>
    """
    source = {
        "id": "list",
        "url": "https://example.com/events",
        "selectors": {"item": ".event", "title": ".title", "date": ".date", "venue": ".place", "excerpt": "p", "url": ".title"},
    }
    rows = extract_html_candidates(html, source, "now")
    assert [(row.raw_title, row.raw_date_text, row.city, row.url) for row in rows] == [
        ("跨境增长沙龙", "2026.09.20", "杭州", "https://example.com/a")
    ]


def test_public_json_api_field_mapping():
    payload = {"items": [{"title": "Dev Event", "start": "2026-11-07", "city": "上海", "link": "/dev"}]}
    source = {
        "id": "api",
        "url": "https://example.com/api",
        "params": {"items_path": "items", "field_map": {"title": "title", "date_start": "start", "city": "city", "url": "link"}},
    }
    rows = extract_json_rows(payload, source, "now")
    assert len(rows) == 1
    assert rows[0].raw_title == "Dev Event"
    assert rows[0].date_start == "2026-11-07"
    assert rows[0].url == "https://example.com/dev"


def test_multi_url_source_continues_after_one_url_fails(monkeypatch):
    class FakeClient:
        def get_text(self, url):
            if url.endswith("bad"):
                raise RuntimeError("HTTP Error 404: Not Found")
            return '<div><a href="/event">AI Summit 2026-09-10</a></div>'

    adapter = HtmlListAdapter()
    monkeypatch.setattr(adapter, "_client", lambda source: FakeClient())
    monkeypatch.setattr(adapter, "_check_robots", lambda client, url: None)
    rows = adapter.fetch(
        {"id": "multi", "urls": ["https://example.com/bad", "https://example.com/good"], "region_hint": ["上海"]},
        AdapterWindow(start=date(2026, 8, 18), end=date(2026, 12, 16)),
    )
    assert len(rows) == 1
