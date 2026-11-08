"""Weekly drop windows and dated events, plus sealed items TCGplayer lists as presale."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .common import DATA_DIR, EASTERN, fold, iso, load_json


def load_windows() -> list[dict]:
    return load_json(DATA_DIR / "windows.json")["windows"]


def load_events() -> dict:
    return load_json(DATA_DIR / "events.json")


def occurrence(window: dict, day: date) -> dict:
    start = datetime.combine(day, time.fromisoformat(window["start"]), tzinfo=EASTERN)
    end = datetime.combine(day, time.fromisoformat(window["end"]), tzinfo=EASTERN)
    return {
        "key": f"{window['id']}:{day.isoformat()}",
        "id": window["id"],
        "store": window["store"],
        "title": window["title"],
        "status": window["status"],
        "note": window["note"],
        "action": window["action"],
        "link": window["link"],
        "weekday": day.weekday(),
        "remind": day.weekday() in window.get("remind_days", []),
        "remind_minutes": window.get("remind_minutes", 30),
        "start": iso(start),
        "end": iso(end),
    }


def upcoming_windows(windows: list[dict], now: datetime, days: int = 14) -> list[dict]:
    today = now.astimezone(EASTERN).date()
    found = []
    for offset in range(days + 1):
        day = today + timedelta(days=offset)
        for window in windows:
            if day.weekday() not in window["days"]:
                continue
            entry = occurrence(window, day)
            ends = datetime.combine(day, time.fromisoformat(window["end"]), tzinfo=EASTERN)
            if ends > now:
                found.append(entry)
    return sorted(found, key=lambda entry: entry["start"])


def presale_events(presale: list[dict], curated: list[dict], today: date) -> list[dict]:
    """Group TCGplayer presale listings by date, skipping anything already curated."""
    covered = [fold(phrase) for event in curated for phrase in event.get("covers", [])]
    by_date: dict[str, list[str]] = {}
    for item in presale:
        if date.fromisoformat(item["release"]) < today:
            continue
        if any(phrase in fold(item["name"]) for phrase in covered):
            continue
        by_date.setdefault(item["release"], []).append(item["name"])

    events = []
    for release, names in sorted(by_date.items()):
        events.append(
            {
                "id": f"tcgplayer-{release}",
                "title": ", ".join(names[:3]) + (f", and {len(names) - 3} more" if len(names) > 3 else ""),
                "date": release,
                "kind": "release",
                "status": "listed",
                "note": "Estimated ship date from TCGplayer's presale listing.",
                "sources": [{"label": "TCGplayer via TCGCSV", "url": "https://tcgcsv.com/"}],
            }
        )
    return events


def upcoming_events(curated: list[dict], presale: list[dict], now: datetime) -> list[dict]:
    today = now.astimezone(EASTERN).date()
    combined = list(curated) + presale_events(presale, curated, today)
    current = [event for event in combined if date.fromisoformat(event.get("end") or event["date"]) >= today]
    return sorted(current, key=lambda event: event["date"])
