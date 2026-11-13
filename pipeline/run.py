"""Scheduled entry point: refresh feeds and prices, then rebuild the site."""
from __future__ import annotations

import os

from . import accessories
from . import tcg_market
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
    # Which sources are resting after repeated failures carries over from run to run.
    rests = load_json(DATA_DIR / "source_rests.json", {})
    fresh, health = fetch_sources(sources, now, rests=rests)
    save_json(DATA_DIR / "source_rests.json", rests)
    items = merge_items(stored, fresh, sources, rules, now)
    save_json(DATA_DIR / "items.json", {"items": items})

    refresh = os.environ.get("REFRESH_PRICES") == "true"
    market = refresh_if_due(now, force=refresh)
    accessory_list = accessories.refresh_if_due(now, force=refresh)
    tcg_list = tcg_market.refresh_if_due(now, force=refresh)
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
    print(f"Accessories priced: {len(accessory_list.get('items', []))}.")
    print(f"Other-TCG sealed priced: {len(tcg_list.get('products', []))}.")


if __name__ == "__main__":
    main()
