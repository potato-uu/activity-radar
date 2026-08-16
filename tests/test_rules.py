from activity_radar.rules import make_id, merge_events, prepare_event, same_event
from activity_radar.schema import Event


def event(name="Event", date_start="2026-09-01", url="https://example.com", **extra):
    values = {"name": name, "date_start": date_start, "date_end": date_start, "url": url, "city": "上海", "acquisition_score": 7, "ecosystem_score": 5, "tier": "B", "reason": "两句理由。相关性明确."}
    values.update(extra)
    return Event(id=make_id(name, date_start, url), **values)


def test_same_source_page_different_events_are_not_duplicates():
    assert not same_event(event("Morketing Growth", "2026-08-25"), event("Morketing AI Salon", "2026-09-12"))


def test_same_event_repeated_run_is_unchanged():
    first = event()
    second = event()
    merged, stats = merge_events([], [first], {"上海"}, {})
    merged, stats = merge_events(merged, [second], {"上海"}, {})
    assert len(merged) == 1
    assert stats["unchanged"] == 1
    assert stats["new"] == 0


def test_changed_date_marks_changed():
    first = event()
    second = event(date_start="2026-09-02", date_end="2026-09-02")
    merged, _ = merge_events([], [first], {"上海"}, {})
    merged, stats = merge_events(merged, [second], {"上海"}, {})
    assert len(merged) == 1
    assert stats["changed"] == 1
    assert merged[0].status == "changed"
    assert merged[0].date_start == "2026-09-02"


def test_d_tier_is_dropped_and_webinar_is_c():
    weak = event("Weak", acquisition_score=2, ecosystem_score=3, tier="D")
    webinar = event("Webinar", event_type="webinar", acquisition_score=9, ecosystem_score=9, tier="A")
    webinar = prepare_event(webinar.to_dict(), "fixture", "2026-08-16T00:00:00+00:00", {})
    merged, stats = merge_events([], [weak, webinar], {"上海"}, {})
    assert stats["dropped"] + stats["invalid"] == 1
    assert merged[0].tier == "C"
