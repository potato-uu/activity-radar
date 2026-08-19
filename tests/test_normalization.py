from datetime import date

from activity_radar.normalization import normalize_raw_candidate, parse_date_text
from activity_radar.adapters.base import RawCandidate


def test_parse_date_text_supports_required_chinese_formats():
    anchor = date(2026, 7, 1)
    assert parse_date_text("8月18日", anchor) == ("2026-08-18", "2026-08-18", "day")
    assert parse_date_text("2026.09.22", anchor) == ("2026-09-22", "2026-09-22", "day")
    assert parse_date_text("9月22日-24日", anchor) == ("2026-09-22", "2026-09-24", "day")
    assert parse_date_text("8.22上海AI实训", anchor) == ("2026-08-22", "2026-08-22", "day")
    assert parse_date_text("本周日0621", date(2026, 6, 15)) == ("2026-06-21", "2026-06-21", "day")


def test_parse_date_text_prefers_explicit_year_in_title():
    assert parse_date_text("2025年11月15日 Free registration Google DevFest", date(2026, 8, 19)) == (
        "2025-11-15",
        "2025-11-15",
        "day",
    )


def test_normalize_raw_candidate_keeps_unknown_date_for_llm():
    raw = RawCandidate(
        source_id="test",
        raw_title="Unknown date",
        raw_date_text="待定",
        url="https://example.com/unknown",
        fetched_at="2026-08-18T00:00:00+00:00",
    )
    normalized = normalize_raw_candidate(raw, date(2026, 8, 18))
    assert normalized["date_start"] == ""
    assert normalized["date_precision"] == "unknown"


def test_normalize_raw_candidate_maps_city_and_type():
    raw = RawCandidate(
        source_id="test",
        raw_title="AI meetup",
        raw_date_text="2026-09-01",
        city="Shanghai",
        venue="徐汇区",
        url="https://example.com/event",
        raw_excerpt="开发者交流活动",
        fetched_at="2026-08-18T00:00:00+00:00",
    )
    normalized = normalize_raw_candidate(raw, date(2026, 8, 18))
    assert normalized["city"] == "上海"
    assert normalized["event_type"] == "沙龙·meetup"


def test_title_city_overrides_adapter_default_city():
    raw = RawCandidate(
        source_id="cifnews-ccee",
        raw_title="eMAG2026中国卖家峰会—深圳",
        raw_date_text="2026-08-20",
        date_start="2026-08-20",
        url="https://example.com/emag",
        fetched_at="now",
        city="上海",
    )
    assert normalize_raw_candidate(raw, date(2026, 8, 18))["city"] == "深圳"


def test_amz123_title_strips_trailing_date_and_city_but_keeps_date_fields():
    raw = RawCandidate(
        source_id="amz123",
        raw_title="2026拉美跨境电商赋能大会·杭州站 2026-08-27 浙江省杭州市",
        raw_date_text="",
        url="https://www.amz123.com/hd/example",
        fetched_at="now",
        city="上海",
    )
    normalized = normalize_raw_candidate(raw, date(2026, 8, 19))
    assert normalized["name"] == "2026拉美跨境电商赋能大会·杭州站"
    assert normalized["date_start"] == "2026-08-27"
    assert normalized["city"] == "杭州"
