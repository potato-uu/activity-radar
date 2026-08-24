from __future__ import annotations

import html
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .rules import is_valid_candidate
from .schema import Event


def render_timeline(events: list[Event], output: Path, generated_at: str, scoring: dict[str, Any] | None = None) -> None:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.status == "cancelled" or event.tier == "D" or not is_valid_candidate(event.to_dict(), scoring or {})[0]:
            continue
        grouped[(event.date_start or "未定")[:7]].append(event)
    cards: list[str] = []
    nearby = {"杭州", "苏州", "南京", "宁波", "无锡", "合肥", "嘉兴", "南通"}
    for month in sorted(grouped):
        cards.append(f"<section class='month'><h2>{html.escape(month)}</h2>")
        for event in grouped[month]:
            region = "上海" if event.city == "上海" else ("周边" if event.city in nearby else "其他")
            date_label = event.date_start or "日期待定"
            if event.status == "expected" or event.date_precision == "month":
                date_label += "（日期待官宣）"
            if event.is_series or event.occurrences:
                date_label += "（系列：" + "、".join(event.occurrences or [event.date_start]) + "）"
            badges = [f"<span class='tier tier-{event.tier}'>{event.tier}</span>"]
            badges.extend([
                f"<span class='score acquisition'>获客 {event.acquisition_score:g}</span>",
                f"<span class='score ecosystem'>资源 {event.ecosystem_score:g}</span>",
                f"<span class='action'>{html.escape(event.action)}</span>",
            ])
            if event.status == "expected":
                badges.append("<span class='status expected'>expected</span>")
            if event.event_type == "side_event":
                badges.append("<span class='status side-event'>side event</span>")
            if event.is_series or event.occurrences:
                badges.append("<span class='status series'>系列</span>")
            if event.needs_review:
                badges.append("<span class='status review'>⚠️ needs_review</span>")
            related = f"<p class='related' data-related-to='{html.escape(event.related_to)}'>related_to: {html.escape(event.related_to)}</p>" if event.related_to else ""
            cards.append(
                "<article class='event' data-city='{city}' data-region='{region}' data-tier='{tier}' data-type='{kind}' "
                "data-status='{status}' data-acq='{acq}' data-eco='{eco}'>"
                "<div class='date'>{start}{end}</div><div class='body'>"
                "<h3><a href='{url}' rel='noreferrer'>{name}</a></h3>"
                "<p class='meta'>{city} · {venue} · {organizer} · {kind}</p>"
                "<div class='badges'>{badges}</div><p>{reason}</p>{related}"
                "</div></article>".format(
                    city=html.escape(event.city), tier=event.tier, kind=html.escape(event.event_type),
                    acq=event.acquisition_score, eco=event.ecosystem_score,
                    region=html.escape(region), status=html.escape(event.status),
                    start=html.escape(date_label),
                    end=(f" - {html.escape(event.date_end)}" if event.date_end and event.date_end != event.date_start else ""),
                    url=html.escape(event.url, quote=True), name=html.escape(event.name), venue=html.escape(event.venue),
                    organizer=html.escape(event.organizer), badges="".join(badges), reason=html.escape(event.reason), related=related,
                )
            )
        cards.append("</section>")
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>上海 BD 活动雷达</title><style>
:root {{ color-scheme: light; --ink:#17202a; --muted:#66717c; --line:#dce2e7; --blue:#2166d1; --green:#177245; --amber:#986500; --red:#a52a2a; }}
* {{ box-sizing:border-box; }} body {{ margin:0; font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:#f7f9fb; }}
main {{ max-width:1040px; margin:0 auto; padding:32px 20px 64px; }} header {{ display:flex; justify-content:space-between; align-items:flex-end; gap:20px; border-bottom:1px solid var(--line); padding-bottom:20px; }}
h1 {{ margin:0; font-size:28px; }} h2 {{ margin:28px 0 10px; font-size:19px; }} .updated {{ color:var(--muted); font-size:13px; }}
.filters {{ display:flex; flex-wrap:wrap; gap:8px; margin:20px 0; }} select {{ border:1px solid var(--line); border-radius:6px; background:white; padding:8px 10px; color:var(--ink); }}
.event {{ display:grid; grid-template-columns:140px 1fr; gap:18px; padding:16px 0; border-top:1px solid var(--line); }} .date {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
h3 {{ margin:0 0 5px; font-size:18px; }} h3 a {{ color:var(--ink); }} .meta {{ color:var(--muted); margin:0 0 9px; }} .badges {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }}
 .badges span {{ display:inline-block; border:1px solid var(--line); border-radius:4px; padding:2px 7px; font-size:12px; background:white; }} .tier-A {{ border-color:#c34b4b !important; color:var(--red); }} .tier-B {{ border-color:#c69827 !important; color:var(--amber); }} .acquisition {{ color:var(--blue); }} .ecosystem {{ color:var(--green); }} .status.expected {{ color:var(--amber); }} .status.side-event {{ color:var(--green); }} .status.review {{ color:var(--red); }} .related {{ color:var(--muted); font-size:13px; margin:6px 0 0; }}
@media(max-width:640px) {{ header {{ display:block; }} .event {{ grid-template-columns:1fr; gap:4px; }} }}
</style></head><body><main><header><div><h1>上海 BD 活动雷达</h1><div class="updated">未来视图 · 生成于 {html.escape(generated_at)}</div></div></header>
<div class="filters"><select id="city"><option value="">全部城市</option><option>上海</option><option>周边</option><option>其他</option></select><select id="tier"><option value="">全部 Tier</option><option>A</option><option>B</option><option>C</option></select><select id="type"><option value="">全部类型</option><option>展会</option><option>峰会</option><option>沙龙·meetup</option><option>开发者大会</option><option>side_event</option><option>webinar</option></select><select id="line"><option value="">全部线别</option><option value="acq">获客线 >= 6</option><option value="eco">资源线 >= 6</option></select></div>
<div id="events">{''.join(cards) or '<p>暂无符合条件的活动。</p>'}</div></main><script>
const apply=()=>{{const city=document.querySelector('#city').value,tier=document.querySelector('#tier').value,type=document.querySelector('#type').value,line=document.querySelector('#line').value;document.querySelectorAll('.event').forEach(e=>{{const ok=(!city||e.dataset.region===city)&&(!tier||e.dataset.tier===tier)&&(!type||e.dataset.type===type)&&(!line||(line==='acq'?+e.dataset.acq>=6:+e.dataset.eco>=6));e.hidden=!ok;}})}};document.querySelectorAll('select').forEach(x=>x.addEventListener('change',apply));
</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
