"""Send new feed items, drop reminders, and the daily summaries to Discord."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, timedelta

from .calendar_data import load_events, load_windows, upcoming_events, upcoming_windows
from .common import CONFIG_DIR, DATA_DIR, EASTERN, SITE_URL, format_eastern, load_json, parse_iso, save_json, utcnow
from .discord_api import Discord, DiscordError

CHANNEL_FOR_CATEGORY = {
    "preorders": "preorders",
    "restocks": "live-restocks",
    "deals": "deals",
    "releases": "release-calendar",
    "market": "market-moves",
    "news": "news",
}
COLORS = {
    "preorders": 0x2447D8,
    "restocks": 0xD9480F,
    "deals": 0x12703F,
    "releases": 0x7048E8,
    "market": 0x8A5A00,
    "news": 0x5D6472,
    "hot": 0xC92A2A,
}
STATUS_LABELS = {"official": "Official", "reported": "Reported", "estimate": "Estimate", "listed": "Listed"}
FIRST_RUN_PER_CHANNEL = 3
MAX_ITEM_POSTS_PER_RUN = 30
DIGEST_HOUR_EASTERN = 7
KEEP_ITEM_IDS = 4000
KEEP_KEYS = 800


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def money(value) -> str:
    return f"${value:,.2f}"


def signed_money(value) -> str:
    return f"{'+' if value >= 0 else '−'}${abs(value):,.2f}"


def item_embed(item: dict, hot: bool = False) -> dict:
    source = item.get("publisher") or item["source"]
    meta = source + (f" · {', '.join(item['stores'])}" if item["stores"] else "")
    return {
        "title": clip(item["title"], 256),
        "url": item["link"],
        "color": COLORS["hot"] if hot else COLORS.get(item["category"], COLORS["news"]),
        "description": clip(meta, 300),
        "timestamp": item["published"],
    }


def plan_item_posts(items: list[dict], posted: set, first_run: bool):
    """Return (channel, item, hot) posts, oldest first, and the ids now considered handled."""
    pending = [item for item in items if item["id"] not in posted]
    if first_run:
        counts: dict[str, int] = {}
        chosen = []
        for item in pending:
            channel = CHANNEL_FOR_CATEGORY[item["category"]]
            if counts.get(channel, 0) < FIRST_RUN_PER_CHANNEL:
                counts[channel] = counts.get(channel, 0) + 1
                chosen.append(item)
        handled = {item["id"] for item in pending}
    else:
        chosen = pending[:MAX_ITEM_POSTS_PER_RUN]
        handled = {item["id"] for item in chosen}

    posts = []
    for item in reversed(chosen):
        posts.append((CHANNEL_FOR_CATEGORY[item["category"]], item, False))
        if item["hot"]:
            posts.append(("hot-alerts", item, True))
    return posts, handled


def due_reminders(windows: list[dict], now, keys: set) -> list[dict]:
    due = []
    for window in windows:
        if not window["remind"] or f"window:{window['key']}" in keys:
            continue
        start = parse_iso(window["start"])
        if start - timedelta(minutes=window["remind_minutes"]) <= now < start:
            due.append(window)
    return due


def reminder_embed(window: dict) -> dict:
    start, end = parse_iso(window["start"]), parse_iso(window["end"])
    return {
        "title": f"{window['store']}: {window['title']}",
        "url": window["link"],
        "color": COLORS["restocks"],
        "description": clip(
            f"**{format_eastern(start)} to {format_eastern(end, with_day=False)}**\n\n{window['action']}\n\n"
            f"{STATUS_LABELS[window['status']]}: {window['note']}",
            1500,
        ),
    }


def describe_event(event: dict) -> str:
    start = date.fromisoformat(event["date"])
    prefix = "Around " if event.get("approx") else ""
    return f"**{prefix}{start:%b} {start.day}** · {event['title']} ({STATUS_LABELS[event['status']]})"


def digest_embed(now, windows: list[dict], events: list[dict], products: list[dict]) -> dict:
    today = now.astimezone(EASTERN).date()
    todays = [w for w in windows if parse_iso(w["start"]).astimezone(EASTERN).date() == today]
    window_lines = [
        f"**{format_eastern(parse_iso(w['start']), with_day=False)}** · {w['store']}: {w['title']}" for w in todays
    ] or ["No regular windows today."]
    soon = [e for e in events if date.fromisoformat(e["date"]) <= today + timedelta(days=7)]
    event_lines = [describe_event(e) for e in soon] or ["Nothing dated in the next 7 days."]
    picks = [p for p in products if p["current"] and p["profit"] > 0][:5]
    pick_lines = [f"{signed_money(p['profit'])} · {p['name']}" for p in picks] or ["Nothing at retail clears a profit right now."]
    return {
        "title": f"Today: {today:%A, %B} {today.day}",
        "url": SITE_URL,
        "color": COLORS["preorders"],
        "fields": [
            {"name": "Windows today", "value": clip("\n".join(window_lines), 1000)},
            {"name": "Next 7 days", "value": clip("\n".join(event_lines), 1000)},
            {"name": "Best at retail price", "value": clip("\n".join(pick_lines), 1000)},
        ],
        "footer": {"text": "Times are Eastern. Reported windows aren't guaranteed."},
    }


def flip_embed(products: list[dict]) -> dict:
    rows = [p for p in products if p["current"]][:15]
    lines = [f"`{signed_money(p['profit']):>9}` {clip(p['name'], 62)} ({money(p['msrp'])} → {money(p['market'])})" for p in rows]
    return {
        "title": "Flip list: estimated profit at retail price",
        "url": SITE_URL + "#flip",
        "color": COLORS["deals"],
        "description": clip("\n".join(lines) or "No price data yet.", 3900),
        "footer": {"text": "After eBay fees, shipping, and sales tax. Default assumptions."},
    }


def event_fingerprint(event: dict) -> str:
    fields = {key: event.get(key) for key in ("title", "date", "end", "status", "msrp")}
    return hashlib.sha1(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:12]


def event_embed(event: dict, changed: bool) -> dict:
    sources = " · ".join(f"[{s['label']}]({s['url']})" for s in event.get("sources", []))
    return {
        "title": clip(f"{'Date update' if changed else 'New date'}: {event['title']}", 256),
        "url": SITE_URL + "#calendar",
        "color": COLORS["releases"],
        "description": clip(f"{describe_event(event)}\n\n{event.get('note', '')}\n\n{sources}", 2000),
    }


def load_webhooks(api: Discord, guild_id: str, webhook_name: str) -> dict:
    names = {channel["id"]: channel["name"] for channel in api.channels(guild_id)}
    return {
        names[hook["channel_id"]]: hook
        for hook in api.guild_webhooks(guild_id)
        if hook.get("name") == webhook_name and hook.get("token") and hook["channel_id"] in names
    }


def run(now, send, items, windows, events, products, state) -> None:
    """Decide what to post and record it. Called with a send(channel, payload) function."""
    first_run = not state.get("initialized")
    posted = set(state.get("posted_items", []))
    keys = set(state.get("posted_keys", []))
    fingerprints = dict(state.get("event_fingerprints", {}))
    known_products = set(state.get("known_products", []))

    posts, handled = plan_item_posts(items, posted, first_run)
    try:
        for channel, item, hot in posts:
            send(channel, {"embeds": [item_embed(item, hot)]})
        posted |= handled

        for window in due_reminders(windows, now, keys):
            send("drop-reminders", {"embeds": [reminder_embed(window)]})
            keys.add(f"window:{window['key']}")

        local = now.astimezone(EASTERN)
        day = local.date().isoformat()
        if local.hour >= DIGEST_HOUR_EASTERN and f"digest:{day}" not in keys:
            send("today", {"embeds": [digest_embed(now, windows, events, products)]})
            keys.add(f"digest:{day}")
            if products:
                send("flip-list", {"embeds": [flip_embed(products)]})

        for event in events:
            fingerprint = event_fingerprint(event)
            previous = fingerprints.get(event["id"])
            if previous != fingerprint:
                send("release-calendar", {"embeds": [event_embed(event, changed=previous is not None)]})
                fingerprints[event["id"]] = fingerprint

        fresh_products = [p for p in products if p["id"] not in known_products]
        if not first_run:
            for product in fresh_products[:10]:
                send(
                    "release-calendar",
                    {"embeds": [{
                        "title": clip(f"New sealed listing: {product['name']}", 256),
                        "url": product["url"] or SITE_URL,
                        "color": COLORS["releases"],
                        "description": f"Retail {money(product['msrp'])} · Market {money(product['market'])}",
                    }]},
                )
        known_products |= {p["id"] for p in products}
        state["initialized"] = True
    finally:
        state["posted_items"] = sorted(posted)[-KEEP_ITEM_IDS:]
        state["posted_keys"] = sorted(keys)[-KEEP_KEYS:]
        state["event_fingerprints"] = fingerprints
        state["known_products"] = sorted(known_products)


def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        print("Discord isn't set up yet; skipping.")
        return 0

    now = utcnow()
    api = Discord(token)
    config = load_json(CONFIG_DIR / "discord.json")
    state_path = DATA_DIR / "state.json"
    state = load_json(state_path, {})
    items = load_json(DATA_DIR / "items.json", {"items": []})["items"]
    market = load_json(DATA_DIR / "market.json", {"products": [], "presale": []})
    calendar = load_events()
    windows = upcoming_windows(load_windows(), now)
    events = upcoming_events(calendar["events"], market.get("presale", []), now)

    try:
        webhooks = load_webhooks(api, guild_id, config["webhook_name"])
        if not webhooks:
            print("No webhooks found. Run the Set up Discord workflow first.")
            return 0

        def send(channel: str, payload: dict) -> None:
            if channel in webhooks:
                api.post(webhooks[channel], payload)

        run(now, send, items, windows, events, market.get("products", []), state)
    except DiscordError as error:
        print(f"Discord posting stopped: {error}")
        return 1
    finally:
        save_json(state_path, state)
    print("Discord is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
