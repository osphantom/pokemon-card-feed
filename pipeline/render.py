"""Assemble the static site: dashboard data, RSS feeds, and the calendar file."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta

from .common import CONFIG_DIR, DATA_DIR, SITE_DIR, SITE_URL, WEB_DIR, fold, format_eastern, iso, load_json, parse_iso
from .ics import build_calendar
from .rss import render_feed

CATEGORY_FEEDS = {
    "promos": ("Free promos", "Free promo cards and gifts with purchase."),
    "raffles": ("Raffles and invites", "Drawings, raffles, and invites you can sign up for."),
    "preorders": ("Preorders", "Preorder openings and early access."),
    "restocks": ("Restocks and drops", "Restocks, queues, and drawings."),
    "deals": ("Deals", "Sealed card deals."),
    "releases": ("Releases", "New sets, products, and dates."),
    "market": ("Market", "Prices, reprints, and market news."),
    "news": ("News", "General Pokémon TCG news."),
}
PUBLIC_ITEM_FIELDS = ("id", "title", "link", "source", "publisher", "published", "category", "stores", "hot")
DASHBOARD_ITEM_LIMIT = 400
FEED_ITEM_LIMIT = 150


def safe_json(data) -> str:
    """JSON that can sit inside a script tag even if a headline contains markup."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def stamp_asset_version() -> None:
    """Tie each page to its exact script and stylesheet so browsers can't mix versions."""
    digest = hashlib.sha1()
    for name in ("assets/app.js", "assets/styles.css"):
        digest.update((SITE_DIR / name).read_bytes())
    for page in ("index.html", "playbook.html"):
        path = SITE_DIR / page
        path.write_text(path.read_text(encoding="utf-8").replace("__ASSET_VERSION__", digest.hexdigest()[:10]), encoding="utf-8")


def enrich_drawings(market: dict) -> list[dict]:
    """Attach today's market price to each item from the most recent store drawings."""
    products = market.get("products", [])
    drawings = load_json(DATA_DIR / "retail_watch.json")["drawings"]
    enriched = []
    for drawing in drawings:
        items = []
        for item in drawing["items"]:
            needle = fold(item["match"])
            exact = [product for product in products if fold(product["name"]) == needle]
            partial = [product for product in products if needle in fold(product["name"])]
            # Variants can differ widely in price, so a loose match uses the cheapest.
            match = (exact or sorted(partial, key=lambda product: product["market"]) or [None])[0]
            items.append(
                {
                    "name": item["name"],
                    "price": item["price"],
                    "market": match["market"] if match else None,
                    "ship": match["ship"] if match else None,
                }
            )
        enriched.append({**drawing, "items": items})
    return enriched


def reminder_items(windows: list[dict], events: list[dict], now: datetime) -> list[dict]:
    """Upcoming windows and dates, written as RSS items with stable identifiers."""
    items = []
    for window in windows:
        start = parse_iso(window["start"])
        if timedelta(0) <= start - now <= timedelta(hours=18):
            items.append(
                {
                    "title": f"Soon: {window['store']} {window['title'].lower()}, {format_eastern(start)}",
                    "link": SITE_URL,
                    "guid": f"window:{window['key']}",
                    "published": iso(start - timedelta(hours=18)),
                    "category": "reminder",
                    "description": f"{window['note']} {window['action']}",
                }
            )
    for event in events:
        day = datetime.fromisoformat(event["date"] + "T07:00:00+00:00")
        if timedelta(0) <= day - now <= timedelta(days=3):
            items.append(
                {
                    "title": f"Coming up: {event['title']} ({event['date']})",
                    "link": SITE_URL,
                    "guid": f"event:{event['id']}",
                    "published": iso(day - timedelta(days=3)),
                    "category": "reminder",
                    "description": event.get("note", ""),
                }
            )
    return items


def to_rss_item(item: dict) -> dict:
    source = item.get("publisher") or item["source"]
    stores = f" Stores: {', '.join(item['stores'])}." if item["stores"] else ""
    return {
        "title": item["title"],
        "link": item["link"],
        "guid": item["id"],
        "published": item["published"],
        "category": item["category"],
        "description": f"From {source}.{stores}",
    }


def newest(items: list[dict], limit: int = FEED_ITEM_LIMIT) -> list[dict]:
    return sorted(items, key=lambda item: item["published"], reverse=True)[:limit]


def write_feeds(items: list[dict], reminders: list[dict], now: datetime) -> list[dict]:
    feeds_dir = SITE_DIR / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)
    rss_items = [to_rss_item(item) for item in items]

    listing = [{"label": "Everything", "path": "feed.xml"}]
    (SITE_DIR / "feed.xml").write_text(
        render_feed("Sealed Feed", "Pokémon TCG releases, preorders, and restocks.", "feed.xml", newest(rss_items + reminders), now),
        encoding="utf-8",
    )

    hot_ids = {item["id"] for item in items if item["hot"]}
    (feeds_dir / "hot.xml").write_text(
        render_feed(
            "Sealed Feed: hot alerts", "Preorders, queues, and drawings worth acting on.", "feeds/hot.xml",
            newest([item for item in rss_items if item["guid"] in hot_ids]), now,
        ),
        encoding="utf-8",
    )
    listing.append({"label": "Hot alerts", "path": "feeds/hot.xml"})

    for category, (label, description) in CATEGORY_FEEDS.items():
        path = f"feeds/{category}.xml"
        selected = newest([item for item in rss_items if item["category"] == category])
        (SITE_DIR / path).write_text(render_feed(f"Sealed Feed: {label}", description, path, selected, now), encoding="utf-8")
        listing.append({"label": label, "path": path})
    return listing


def build_site(now, items, market, windows_all, windows, events, undated, health, events_verified) -> dict:
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    shutil.copytree(WEB_DIR, SITE_DIR)
    stamp_asset_version()

    feeds = write_feeds(items, reminder_items(windows, events, now), now)
    (SITE_DIR / "calendar.ics").write_text(build_calendar(events, windows_all, now), encoding="utf-8", newline="")

    public_market = {key: market.get(key) for key in ("generated_at", "ebay_used", "products")}
    payload = {
        "generated_at": iso(now),
        "verified": events_verified,
        "site_url": SITE_URL,
        "windows": windows,
        "events": events,
        "undated": undated,
        "market": public_market,
        "assumptions": load_json(CONFIG_DIR / "assumptions.json"),
        "drawings": enrich_drawings(market),
        "items": [{field: item.get(field) for field in PUBLIC_ITEM_FIELDS} for item in items[:DASHBOARD_ITEM_LIMIT]],
        "sources": [{"name": record["name"], "ok": record["ok"], "count": record["count"]} for record in health],
        "feeds": feeds,
    }
    index = SITE_DIR / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("__DATA_JSON__", safe_json(payload)), encoding="utf-8")
    return payload
