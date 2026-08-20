from activity_radar.rules import apply_side_event_links, make_id, merge_events, normalize_name, prepare_event, same_event
from activity_radar.schema import Event


def event(name="Sample Event", date_start="2026-09-01", url="https://example.com", **extra):
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
    second = event(date_start="2026-09-15", date_end="2026-09-15")
    merged, _ = merge_events([], [first], {"上海"}, {})
    merged, stats = merge_events(merged, [second], {"上海"}, {})
    assert len(merged) == 1
    assert stats["changed"] == 1
    assert merged[0].status == "changed"
    assert merged[0].date_start == "2026-09-15"


def test_duplicate_key_uses_normalized_name_city_and_one_day_tolerance():
    left = event("2026 Google DevFest 上海", "2026-11-07", city="上海", url="https://a.example/event")
    right = event("2026 Google DevFest 上海！", "2026-11-08", city="上海", url="https://b.example/event")
    far = event("2026 Google DevFest 上海", "2026-11-10", city="上海", url="https://c.example/event")
    other_city = event("2026 Google DevFest 上海", "2026-11-07", city="杭州", url="https://d.example/event")
    assert same_event(left, right)
    assert not same_event(left, far)
    assert not same_event(left, other_city)


def test_normalize_name_removes_punctuation_emoji_and_issue_numbers():
    assert normalize_name("⚡️ ShanghAI Meetup Vol. 25（第25期）") == "shanghaimeetup"


def test_series_occurrences_are_merged_into_one_event():
    first = event("ShanghAI Meetup Vol. 24", "2026-09-01", metadata={"is_series": True})
    second = event("ShanghAI Meetup Vol. 25", "2026-09-08", metadata={"is_series": True})
    merged, stats = merge_events([], [first, second], {"上海"}, {})
    assert len(merged) == 1
    assert stats["new"] == 1
    assert merged[0].metadata["is_series"] is True
    assert merged[0].metadata["occurrences"] == ["2026-09-01", "2026-09-08"]


def test_existing_same_name_multiple_dates_are_collapsed_without_new_candidates():
    first = event("Weekly AI Meetup", "2026-09-01")
    second = event("Weekly AI Meetup", "2026-09-08")
    merged, _ = merge_events([first, second], [], {"上海"}, {})
    assert len(merged) == 1
    assert merged[0].is_series is True
    assert merged[0].occurrences == ["2026-09-01", "2026-09-08"]
    assert merged[0].metadata["merged_event_ids"] == [first.id, second.id]


def test_supply_side_caps_acquisition_at_four():
    scored = event("GEO vendor summit", audience_side="supply", acquisition_score=9, ecosystem_score=7)
    prepared = prepare_event(scored.to_dict(), "test", "2026-08-18T00:00:00+00:00", {})
    assert prepared.acquisition_score == 4


def test_small_open_event_reduces_both_scores_by_two():
    scored = event("Small expo", scale_hint="展商 20", format="open", acquisition_score=8, ecosystem_score=7)
    prepared = prepare_event(scored.to_dict(), "test", "2026-08-18T00:00:00+00:00", {})
    assert prepared.acquisition_score == 6
    assert prepared.ecosystem_score == 5


def test_small_closed_door_event_is_not_penalized():
    scored = event("Founder dinner", scale_hint="人数 80", format="closed_door", acquisition_score=8, ecosystem_score=7)
    prepared = prepare_event(scored.to_dict(), "test", "2026-08-18T00:00:00+00:00", {})
    assert prepared.acquisition_score == 8
    assert prepared.ecosystem_score == 7


def test_city_correction_and_other_city_push_boundary():
    nearby = prepare_event(event(city="杭州", acquisition_score=8, ecosystem_score=7).to_dict(), "test", "now", {})
    other = prepare_event(event(city="北京", acquisition_score=9, ecosystem_score=7).to_dict(), "test", "now", {})
    assert (nearby.acquisition_score, nearby.ecosystem_score) == (7, 6)
    assert (other.acquisition_score, other.ecosystem_score) == (7, 5)
    assert other.metadata["web_only"] is True


def test_score_history_marks_large_adjacent_change_for_review():
    first = prepare_event(event(acquisition_score=5, ecosystem_score=5).to_dict(), "test", "2026-08-18T00:00:00+00:00", {})
    second = event(acquisition_score=8, ecosystem_score=5)
    merged, _ = merge_events([first], [second], {"上海"}, {})
    assert merged[0].needs_review is True
    assert len(merged[0].score_history) == 2


def test_side_event_opportunity_and_related_event_are_linked():
    conference = event("Platform Conference", "2026-09-10", tier="A", event_type="峰会", ecosystem_score=9, metadata={"platform_official": True})
    salon = event("Conference Side Dinner", "2026-09-11", tier="B", event_type="side_event")
    linked = apply_side_event_links([conference, salon])
    assert linked[0].side_event_opportunity is True
    assert linked[1].related_to == linked[0].id


def test_tier_b_is_not_a_side_event_conference_even_when_multiday_and_official():
    conference = event("Tier B Conference", "2026-09-10", date_end="2026-09-12", tier="B", event_type="峰会", side_event_opportunity=True, metadata={"platform_official": True})
    salon = event("Nearby Dinner", "2026-09-11", tier="B", event_type="side_event", related_to=conference.id)
    linked = apply_side_event_links([conference, salon])
    assert linked[0].side_event_opportunity is False
    assert linked[1].related_to == ""


def test_d_tier_is_dropped_and_high_value_webinar_keeps_its_tier():
    weak = event("Weak Summit", acquisition_score=2, ecosystem_score=3, tier="D")
    webinar = event("AI Strategy Webinar", event_type="webinar", acquisition_score=9, ecosystem_score=9, tier="A")
    webinar = prepare_event(webinar.to_dict(), "fixture", "2026-08-16T00:00:00+00:00", {})
    merged, stats = merge_events([], [weak, webinar], {"上海"}, {})
    assert stats["dropped"] + stats["invalid"] == 1
    assert merged[0].tier == "A"


def test_merge_drops_navigation_and_missing_date_candidates():
    bad = event(name="报名中", date_start="", acquisition_score=9, ecosystem_score=9, tier="A")
    merged, stats = merge_events([], [bad], {"上海"}, {"title_blacklist": ["报名中"]})
    assert merged == []
    assert stats["invalid"] == 1
