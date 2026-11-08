"""Scheduled entry point: refresh feeds and prices, then rebuild the site."""
from __future__ import annotations

from .calendar_data import load_events, load_windows, upcoming_events, upcoming_windows
from .classify import load_rules
from .common import CONFIG_DIR, DATA_DIR, load_json, save_json, utcnow
from .feeds import fetch_sources, merge_items
from .market import refresh_if_due
from .render import build_site


def main() -> None:
    now = utcnow()
    sources = load_json(CONFIG_DIR / "sources.json")["sources"]
    rules = load_rules()

    stored = load_json(DATA_DIR / "items.json", {"items": []})["items"]
    fresh, health = fetch_sources(sources, now)
    items = merge_items(stored, fresh, sources, rules, now)
    save_json(DATA_DIR / "items.json", {"items": items})

    market = refresh_if_due(now)
    windows_all = load_windows()
    windows = upcoming_windows(windows_all, now)
    calendar = load_events()
    events = upcoming_events(calendar["events"], market.get("presale", []), now)

    build_site(now, items, market, windows_all, windows, events, calendar["undated"], health, calendar["verified"])

    working = sum(1 for record in health if record["ok"])
    print(f"Sources working: {working} of {len(health)}")
    for record in health:
        if not record["ok"]:
            print(f"  Skipped {record['name']}: {record['status']}")
    print(f"Items kept: {len(items)}. New this run: {len([i for i in items if i['first_seen'] == items[0]['first_seen']]) if items else 0}.")
    print(f"Sealed products priced: {len(market.get('products', []))}.")


if __name__ == "__main__":
    main()
