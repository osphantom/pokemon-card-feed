"""Write a subscribable iCalendar file for dated events and weekly windows."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .common import SITE_URL

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
# Fixed first date for weekly events so their identity doesn't shift between builds.
WEEKLY_ANCHOR = date(2026, 11, 9)
STATUS_LABELS = {"official": "Official", "reported": "Reported", "estimate": "Estimate", "listed": "Listed"}

VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:America/New_York",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:-0500",
    "TZOFFSETTO:-0400",
    "TZNAME:EDT",
    "DTSTART:19700308T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:-0400",
    "TZOFFSETTO:-0500",
    "TZNAME:EST",
    "DTSTART:19701101T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold_line(line: str) -> str:
    """Wrap at 75 octets without splitting a multi-byte character."""
    parts, current, size = [], "", 0
    for character in line:
        width = len(character.encode("utf-8"))
        if size + width > 75:
            parts.append(current)
            current, size = " ", 1
        current += character
        size += width
    parts.append(current)
    return "\r\n".join(parts)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def event_lines(event: dict, now: datetime) -> list[str]:
    start = date.fromisoformat(event["date"])
    last = date.fromisoformat(event.get("end") or event["date"])
    label = STATUS_LABELS.get(event["status"], event["status"].title())
    prefix = "Around: " if event.get("approx") else ""
    details = [f"{label}. {event.get('note', '')}".strip()]
    if event.get("msrp"):
        details.append(f"MSRP ${event['msrp']:.2f}")
    details.extend(f"{source['label']}: {source['url']}" for source in event.get("sources", []))
    return [
        "BEGIN:VEVENT",
        f"UID:{event['id']}@pokemon-card-feed",
        f"DTSTAMP:{stamp(now)}",
        f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
        f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{escape('PKMN: ' + prefix + event['title'])}",
        f"DESCRIPTION:{escape(chr(10).join(details))}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def window_lines(window: dict, now: datetime) -> list[str]:
    days = window.get("calendar_days", [])
    if not days:
        return []
    first = WEEKLY_ANCHOR
    while first.weekday() not in days:
        first += timedelta(days=1)
    start_text = f"{first:%Y%m%d}T{window['start'].replace(':', '')}00"
    end_text = f"{first:%Y%m%d}T{window['end'].replace(':', '')}00"
    byday = ",".join(WEEKDAY_CODES[day] for day in sorted(days))
    details = "\n".join([f"Reported, not guaranteed. {window['note']}", window["action"], window["link"], SITE_URL + "playbook.html"])
    return [
        "BEGIN:VEVENT",
        f"UID:window-{window['id']}@pokemon-card-feed",
        f"DTSTAMP:{stamp(now)}",
        f"DTSTART;TZID=America/New_York:{start_text}",
        f"DTEND;TZID=America/New_York:{end_text}",
        f"RRULE:FREQ=WEEKLY;BYDAY={byday}",
        f"SUMMARY:{escape('PKMN: ' + window['store'] + ' ' + window['title'].lower())}",
        f"DESCRIPTION:{escape(details)}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def build_calendar(events: list[dict], windows: list[dict], now: datetime) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//pokemon-card-feed//Sealed Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Pokémon sealed drops",
        "X-WR-TIMEZONE:America/New_York",
        *VTIMEZONE,
    ]
    for event in events:
        lines.extend(event_lines(event, now))
    for window in windows:
        lines.extend(window_lines(window, now))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
