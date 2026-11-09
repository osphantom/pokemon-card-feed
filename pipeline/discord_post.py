"""Keep the Discord server current: live boards, store channels, and drop pings."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, timedelta

from .calendar_data import load_events, load_windows, upcoming_events, upcoming_windows
from .common import CONFIG_DIR, DATA_DIR, EASTERN, SITE_URL, format_eastern, load_json, parse_iso, save_json, utcnow
from .costco_stock import BASE_URL as COSTCO_LOOKUP_URL
from .costco_stock import refresh_if_due as refresh_costco
from .costco_stock import status_of
from .discord_api import NOT_FOUND, Discord, DiscordError, normalized

BUYING_CATEGORIES = {"preorders", "restocks", "deals"}
GENERAL_CHANNEL = {
    "preorders": "preorders",
    "deals": "deals",
    "releases": "new-products",
    "market": "market-prices",
    "news": "pokemon-news",
}
STORE_CHANNEL = {
    "Pokémon Center": "pokemon-center",
    "Walmart": "walmart",
    "Target": "target",
    "Sam's Club": "sams-club",
    "Costco": "costco",
}
OTHER_STORES = "other-stores"
PING_CHANNEL = "drop-pings"
# Only sources that post the moment something goes live can ping.
ALERT_SOURCES = {"reddit-deals", "dropradar-newsletter"}
PING_FRESH_FOR = timedelta(hours=3)
MAX_PINGS_PER_RUN = 3
MORNING_HOUR_EASTERN = 7
FIRST_RUN_PER_CHANNEL = 3
MAX_ITEM_POSTS_PER_RUN = 30
KEEP_ITEM_IDS = 4000
KEEP_KEYS = 800
BEST_BUYS = 10
WORST_BUYS = 5
SKIP_LIST_MIN_PRICE = 13

COLORS = {
    "preorders": 0x2447D8,
    "restocks": 0xD9480F,
    "deals": 0x12703F,
    "releases": 0x7048E8,
    "market": 0x8A5A00,
    "news": 0x5D6472,
    "ping": 0xC92A2A,
}
STATUS_LABELS = {"official": "Official", "reported": "Reported", "estimate": "Estimate", "listed": "Listed"}


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def money(value) -> str:
    return f"${value:,.2f}"


def signed_money(value) -> str:
    return f"{'+' if value >= 0 else '−'}${abs(value):,.2f}"


def discord_time(iso_text: str, style: str) -> str:
    """Discord shows this in each reader's own time zone, and relative ones count down live."""
    return f"<t:{int(parse_iso(iso_text).timestamp())}:{style}>"


def when(window: dict) -> str:
    return f"{discord_time(window['start'], 'F')} ({discord_time(window['start'], 'R')})"


def with_ping(payload: dict) -> dict:
    return {**payload, "content": "@everyone", "allowed_mentions": {"parse": ["everyone"]}}


def channels_for(item: dict) -> list[str]:
    """Each item goes to its store's channel and to one general channel."""
    targets: list[str] = []
    if item["category"] in BUYING_CATEGORIES:
        for store in item["stores"]:
            channel = STORE_CHANNEL.get(store, OTHER_STORES)
            if channel not in targets:
                targets.append(channel)
        if not targets and item["category"] == "restocks":
            targets.append(OTHER_STORES)
    general = GENERAL_CHANNEL.get(item["category"])
    if general:
        targets.append(general)
    return targets


def item_payload(item: dict, color_key: str | None = None) -> dict:
    source = item.get("publisher") or item["source"]
    meta = source + (f" · {', '.join(item['stores'])}" if item["stores"] else "")
    return {
        "embeds": [{
            "title": clip(item["title"], 256),
            "url": item["link"],
            "color": COLORS.get(color_key or item["category"], COLORS["news"]),
            "description": clip(meta, 300),
            "timestamp": item["published"],
        }]
    }


def should_ping(item: dict, now) -> bool:
    return bool(
        item["hot"]
        and item.get("source_id") in ALERT_SOURCES
        and now - parse_iso(item["published"]) <= PING_FRESH_FOR
    )


def plan_item_posts(items: list[dict], posted: set, first_run: bool, now):
    """Return (channel, item, ping) posts, oldest first, and the ids now handled."""
    pending = [item for item in items if item["id"] not in posted]
    if first_run:
        counts: dict[str, int] = {}
        chosen = []
        for item in pending:
            for channel in channels_for(item):
                if counts.get(channel, 0) < FIRST_RUN_PER_CHANNEL:
                    counts[channel] = counts.get(channel, 0) + 1
                    chosen.append((channel, item, False))
        return list(reversed(chosen)), {item["id"] for item in pending}

    batch = pending[:MAX_ITEM_POSTS_PER_RUN]
    posts, pings = [], 0
    for item in reversed(batch):
        posts.extend((channel, item, False) for channel in channels_for(item))
        if should_ping(item, now) and pings < MAX_PINGS_PER_RUN:
            posts.append((PING_CHANNEL, item, True))
            pings += 1
    return posts, {item["id"] for item in batch}


def one_per_window(windows: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for window in windows:
        if window["id"] not in seen:
            seen.add(window["id"])
            unique.append(window)
    return unique


def describe_event(event: dict) -> str:
    start = date.fromisoformat(event["date"])
    prefix = "Around " if event.get("approx") else ""
    label = f"{start:%b} {start.day}"
    if event.get("end"):
        end = date.fromisoformat(event["end"])
        label += f" to {end:%b} {end.day}"
    price = f" ({money(event['msrp'])})" if event.get("msrp") else ""
    return f"**{prefix}{label}** · {event['title']}{price} · {STATUS_LABELS[event['status']]}"


def next_drops_board(windows: list[dict], events: list[dict]) -> dict:
    upcoming = one_per_window(windows)
    if not upcoming:
        return {"embeds": [{"title": "📅 When are the next drops?", "description": "Nothing scheduled right now.", "color": COLORS["preorders"]}]}
    first = upcoming[0]
    fields = []
    rest = [f"**{w['store']}: {w['title']}** · {w['repeat']}\n{when(w)}" for w in upcoming[1:]]
    if rest:
        fields.append({"name": "The rest of the week", "value": clip("\n\n".join(rest), 1024)})
    dated = [describe_event(event) for event in events[:5]]
    if dated:
        fields.append({"name": "Release dates coming up", "value": clip("\n".join(dated), 1024)})
    return {
        "embeds": [{
            "title": "📅 When are the next drops?",
            "url": SITE_URL,
            "color": COLORS["preorders"],
            "description": clip(
                f"## Next up: {first['store']}\n**{first['title']}** · {first['repeat']}\n{when(first)}\n\n"
                f"**What to do:** {first['action']}",
                1500,
            ),
            "fields": fields,
            "footer": {"text": "This list updates itself. Times show in your own time zone. These are usual times, not promises."},
        }]
    }


def calendar_board(events: list[dict], undated: list[dict]) -> dict:
    lines = [describe_event(event) for event in events] or ["Nothing dated right now."]
    if undated:
        lines.append("\n**Date not set yet**")
        lines.extend(f"{item['title']} · {STATUS_LABELS[item['status']]}" for item in undated)
    return {
        "embeds": [{
            "title": "📆 Every upcoming date",
            "url": SITE_URL + "#calendar",
            "color": COLORS["releases"],
            "description": clip("\n".join(lines), 4000),
            "footer": {"text": "This list updates itself. Official = confirmed. Reported = not guaranteed. Estimate = calculated."},
        }]
    }


def what_to_buy_board(products: list[dict]) -> dict:
    current = sorted((p for p in products if p["current"]), key=lambda p: p["profit"], reverse=True)
    best = [p for p in current if p["profit"] > 0][:BEST_BUYS]
    worst = [p for p in reversed(current) if p["profit"] < 0 and p["msrp"] >= SKIP_LIST_MIN_PRICE][:WORST_BUYS]
    good_lines = [f"**{signed_money(p['profit'])}** · {clip(p['name'], 70)}\nBuy {money(p['msrp'])}, sells about {money(p['market'])}" for p in best]
    bad_lines = [f"**{signed_money(p['profit'])}** · {clip(p['name'], 70)}" for p in worst]
    fields = [{"name": "✅ Best buys at the normal store price", "value": clip("\n".join(good_lines) or "Nothing clears a profit right now.", 1024)}]
    if len(good_lines) > 5:
        fields = [
            {"name": "✅ Best buys at the normal store price", "value": clip("\n".join(good_lines[:5]), 1024)},
            {"name": "✅ More good buys", "value": clip("\n".join(good_lines[5:]), 1024)},
        ]
    if bad_lines:
        fields.append({"name": "❌ Skip these at the normal store price", "value": clip("\n".join(bad_lines), 1024)})
    return {
        "embeds": [{
            "title": "💰 What makes money right now",
            "url": SITE_URL + "#flip",
            "color": COLORS["deals"],
            "description": "What you'd keep after eBay fees, shipping, and sales tax if you bought at the normal store price. Prices above that normal price cut into this.",
            "fields": fields,
            "footer": {"text": "Estimates that update twice a day. Check eBay sold listings before you buy."},
        }]
    }


def short_name(warehouse: dict, abbreviation: str) -> str:
    suffix = f" {abbreviation}"
    return warehouse["name"][: -len(suffix)] if warehouse["name"].endswith(suffix) else warehouse["name"]


def counted(rows: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name} ({units:,})" for name, units in sorted(rows, key=lambda row: -row[1]))


def state_field(abbreviation: str, warehouses: list[dict], value_limit: int):
    here = [(short_name(w, abbreviation), w["hand"]) for w in warehouses if w["hand"] > 0]
    coming = [(short_name(w, abbreviation), w["transit"]) for w in warehouses if w["transit"] > 0]
    ordered = sum(w["order"] for w in warehouses)
    if not here and not coming and not ordered:
        return None
    parts = []
    if here:
        parts.append(f"**In the store:** {counted(here)}")
    if coming:
        parts.append(f"**On the truck:** {counted(coming)}")
    if ordered:
        parts.append(f"**Ordered:** {ordered:,} units")
    total_here = sum(units for _, units in here)
    total_coming = sum(units for _, units in coming)
    return {
        "name": f"{abbreviation}: {total_here:,} in the store · {total_coming:,} on the truck",
        "value": clip("\n".join(parts), value_limit),
    }, total_here + total_coming


def costco_board(data: dict) -> dict:
    # Discord caps a whole embed at 6,000 characters, so states share the room.
    limit = max(120, 3800 // max(1, len(data["states"])))
    built = [
        (abbreviation, state_field(abbreviation, rows, min(limit, 1024)))
        for abbreviation, rows in data["states"].items()
    ]
    ranked = sorted((entry for entry in built if entry[1]), key=lambda entry: -entry[1][1])
    focus = data.get("focus", "")
    fields = [entry[1][0] for entry in ranked if entry[0] == focus] + [entry[1][0] for entry in ranked if entry[0] != focus]
    if focus and not any(entry[0] == focus for entry in ranked):
        fields.insert(0, {
            "name": f"{focus}: nothing on this sheet yet",
            "value": "No warehouse here has stock listed right now. You'll get a ping when that changes.",
        })
    fields = fields[:25]
    area = data.get("area") or "Eastern-time states"
    return {
        "embeds": [{
            "title": f"📦 Costco shipments: {area}",
            "url": COSTCO_LOOKUP_URL,
            "color": COLORS["restocks"],
            "description": clip(
                f"**{data['product']}** · item #{data['item']}\n"
                f"Latest sheet: **{data['sheet_date']}**. Covers {area}.\n\n"
                f"[Look up any Costco by ZIP]({COSTCO_LOOKUP_URL}). Numbers come from shipment sheets, not live "
                "shelves, so call the warehouse with the item number before you drive.",
                1000,
            ),
            "fields": fields or [{"name": "Nothing yet", "value": "No warehouses in these states have stock on this sheet."}],
            "footer": {"text": "Data from PokeRestock. Checked about every 2 hours."},
        }]
    }


def shipment_payload(data: dict, arrivals: list[tuple[str, int]], trucks: list[tuple[str, int]]) -> dict:
    lines = []
    if arrivals:
        lines.append(f"**Just arrived:** {counted(arrivals)}")
    if trucks:
        lines.append(f"**Now on the truck:** {counted(trucks)}")
    return {
        "embeds": [{
            "title": f"📦 New Costco shipments on the {data['sheet_date']} sheet",
            "url": COSTCO_LOOKUP_URL,
            "color": COLORS["restocks"],
            "description": clip("\n\n".join(lines), 3800),
            "footer": {"text": f"Item #{data['item']}. Call the warehouse before you drive."},
        }]
    }


def shipment_ping(data: dict, arrivals: list, trucks: list) -> dict:
    return with_ping({
        "embeds": [{
            "title": "🔔 New Costco shipments",
            "url": COSTCO_LOOKUP_URL,
            "color": COLORS["ping"],
            "description": (
                f"{len(arrivals)} warehouses in your area just got stock and {len(trucks)} have it on the truck. "
                "See which ones in the Costco channel."
            ),
            "footer": {"text": f"{data['product']} · item #{data['item']}"},
        }]
    })


def apply_costco(client, state: dict, data: dict, keys: set) -> None:
    """Update the Costco list. The first check only records where things stand."""
    previous = state.get("costco_status")
    arrivals, trucks, current = [], [], {}
    for abbreviation, warehouses in data["states"].items():
        for warehouse in warehouses:
            status = status_of(warehouse)
            current[warehouse["number"]] = status
            if previous is None:
                continue
            before = previous.get(warehouse["number"], "none")
            label = f"{short_name(warehouse, abbreviation)} {abbreviation}"
            if status == "hand" and before != "hand":
                arrivals.append((label, warehouse["hand"]))
            elif status == "transit" and before in ("none", "order"):
                trucks.append((label, warehouse["transit"]))

    update_board(client, state, "costco", costco_board(data))
    if arrivals or trucks:
        client.send("costco", shipment_payload(data, arrivals, trucks))
        sheet_key = f"costco-sheet:{data['sheet_date']}"
        if sheet_key not in keys:
            client.send(PING_CHANNEL, shipment_ping(data, arrivals, trucks))
            keys.add(sheet_key)
    state["costco_status"] = {**(previous or {}), **current}


def sams_board() -> dict:
    return {
        "embeds": [{
            "title": "🛒 Check Sam's Club stock by club",
            "url": "https://restockd.app/pokemon-in-store",
            "color": COLORS["restocks"],
            "description": (
                "[Restockd's free in-store map](https://restockd.app/pokemon-in-store) shows reported on-order "
                "counts by club for the items it tracks. Search your ZIP, then call the club before you drive.\n\n"
                "When Restockd posts a new Sam's Club list, it will show up in this channel."
            ),
        }]
    }


def spotted_board(store: str, alerts_channel: str) -> dict:
    return {
        "embeds": [{
            "title": f"📍 See what people spotted at {store} near you",
            "url": "https://restockd.app/pokemon-in-store",
            "color": COLORS["restocks"],
            "description": (
                f"There's no public list of what's headed to each {store}. The closest thing is "
                f"[Restockd's free map](https://restockd.app/pokemon-in-store), which shows stock members saw on {store} "
                "shelves near your ZIP, about an hour behind.\n\n"
                f"For instant online alerts, turn on notifications for **{alerts_channel}** in PokePings, or join "
                "[Restockd's free Discord](https://discord.gg/cnek2qsAht)."
            ),
        }]
    }


def morning_windows(windows: list[dict], now) -> list[dict]:
    horizon = now + timedelta(hours=24)
    return [w for w in windows if w["remind"] and now <= parse_iso(w["start"]) < horizon]


def morning_payload(windows: list[dict]) -> dict:
    lines = [f"**{w['store']}: {w['title']}**\n{when(w)}\n{w['action']}" for w in windows]
    return with_ping({
        "embeds": [{
            "title": "🔔 Drops in the next 24 hours",
            "url": SITE_URL,
            "color": COLORS["ping"],
            "description": clip("\n\n".join(lines), 3500),
            "footer": {"text": "These are usual times, not promises."},
        }]
    })


def due_reminders(windows: list[dict], now, keys: set) -> list[dict]:
    due = []
    for window in windows:
        if not window["remind"] or f"window:{window['key']}" in keys:
            continue
        start = parse_iso(window["start"])
        if start - timedelta(minutes=window["remind_minutes"]) <= now < start:
            due.append(window)
    return due


def reminder_payload(window: dict) -> dict:
    return with_ping({
        "embeds": [{
            "title": f"🔔 {window['store']}: {window['title']}",
            "url": window["link"],
            "color": COLORS["ping"],
            "description": clip(
                f"**Starts {discord_time(window['start'], 'R')}** ({format_eastern(parse_iso(window['start']))})\n\n"
                f"**What to do:** {window['action']}",
                1500,
            ),
        }]
    })


def fingerprint(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def event_fingerprint(event: dict) -> str:
    return fingerprint({key: event.get(key) for key in ("title", "date", "end", "status", "msrp")})


def event_change_payload(event: dict, changed: bool) -> dict:
    sources = " · ".join(f"[{s['label']}]({s['url']})" for s in event.get("sources", []))
    return {
        "embeds": [{
            "title": clip(f"{'Date update' if changed else 'New date'}: {event['title']}", 256),
            "url": SITE_URL + "#calendar",
            "color": COLORS["releases"],
            "description": clip(f"{describe_event(event)}\n\n{event.get('note', '')}\n\n{sources}", 2000),
        }]
    }


def update_board(client, state: dict, channel: str, payload: dict) -> None:
    """Keep one message per board and edit it only when its content changes."""
    boards = state.setdefault("boards", {})
    digest = fingerprint(payload)
    entry = boards.get(channel) or {}
    if entry.get("hash") == digest:
        return
    boards[channel] = {"id": client.upsert(channel, payload, entry.get("id")), "hash": digest}


def run(now, client, items, windows, events, undated, products, state, costco=None) -> None:
    """Decide what to post and record it."""
    first_run = not state.get("initialized")
    posted = set(state.get("posted_items", []))
    keys = set(state.get("posted_keys", []))
    fingerprints = dict(state.get("event_fingerprints", {}))
    known_products = set(state.get("known_products", []))

    posts, handled = plan_item_posts(items, posted, first_run, now)
    try:
        update_board(client, state, "next-drops", next_drops_board(windows, events))
        update_board(client, state, "release-calendar", calendar_board(events, undated))
        if products:
            update_board(client, state, "what-to-buy", what_to_buy_board(products))
        update_board(client, state, "sams-club", sams_board())
        update_board(client, state, "walmart", spotted_board("Walmart", "walmart-free"))
        update_board(client, state, "target", spotted_board("Target", "target-free"))
        if costco:
            apply_costco(client, state, costco, keys)

        for channel, item, ping in posts:
            payload = item_payload(item, "ping" if ping else None)
            client.send(channel, with_ping(payload) if ping else payload)
        posted |= handled

        local = now.astimezone(EASTERN)
        morning_key = f"morning:{local.date().isoformat()}"
        if not first_run and local.hour >= MORNING_HOUR_EASTERN and morning_key not in keys:
            coming = morning_windows(windows, now)
            if coming:
                client.send(PING_CHANNEL, morning_payload(coming))
            keys.add(morning_key)

        for window in due_reminders(windows, now, keys):
            if not first_run:
                client.send(PING_CHANNEL, reminder_payload(window))
            keys.add(f"window:{window['key']}")

        for event in events:
            current = event_fingerprint(event)
            previous = fingerprints.get(event["id"])
            if previous != current:
                if not first_run:
                    client.send("release-calendar", event_change_payload(event, changed=previous is not None))
                fingerprints[event["id"]] = current

        if not first_run:
            fresh = [p for p in products if p["id"] not in known_products][:10]
            for product in fresh:
                client.send("new-products", {
                    "embeds": [{
                        "title": clip(f"New sealed listing: {product['name']}", 256),
                        "url": product["url"] or SITE_URL,
                        "color": COLORS["releases"],
                        "description": f"Store price {money(product['msrp'])} · Selling for about {money(product['market'])}",
                    }]
                })
        known_products |= {p["id"] for p in products}
        state["initialized"] = True
    finally:
        state["posted_items"] = sorted(posted)[-KEEP_ITEM_IDS:]
        state["posted_keys"] = sorted(keys)[-KEEP_KEYS:]
        state["event_fingerprints"] = fingerprints
        state["known_products"] = sorted(known_products)


class WebhookClient:
    """Posts by channel key. A channel without a webhook is skipped."""

    def __init__(self, api: Discord, webhooks: dict):
        self.api = api
        self.webhooks = webhooks

    def send(self, channel: str, payload: dict) -> None:
        if channel in self.webhooks:
            self.api.post(self.webhooks[channel], payload)

    def upsert(self, channel: str, payload: dict, message_id):
        webhook = self.webhooks.get(channel)
        if webhook is None:
            return message_id
        if message_id:
            try:
                self.api.edit(webhook, message_id, payload)
                return message_id
            except DiscordError as error:
                if error.status != NOT_FOUND:
                    raise
        return self.api.post(webhook, payload).get("id")


def load_webhooks(api: Discord, guild_id: str, config: dict) -> dict:
    key_by_name = {
        normalized(channel["name"]): channel["key"]
        for category in config["categories"]
        for channel in category["channels"]
    }
    names = {channel["id"]: channel["name"] for channel in api.channels(guild_id)}
    webhooks = {}
    for hook in api.guild_webhooks(guild_id):
        key = key_by_name.get(normalized(names.get(hook["channel_id"], "")))
        if key and hook.get("name") == config["webhook_name"] and hook.get("token"):
            webhooks[key] = hook
    return webhooks


def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        print("Discord isn't set up yet; skipping.")
        return 0

    now = utcnow()
    api = Discord(token)
    state_path = DATA_DIR / "state.json"
    state = load_json(state_path, {})
    items = load_json(DATA_DIR / "items.json", {"items": []})["items"]
    market = load_json(DATA_DIR / "market.json", {"products": [], "presale": []})
    calendar = load_events()
    windows = upcoming_windows(load_windows(), now)
    events = upcoming_events(calendar["events"], market.get("presale", []), now)

    try:
        webhooks = load_webhooks(api, guild_id, load_json(CONFIG_DIR / "discord.json"))
        if not webhooks:
            print("No webhooks found. Run the Set up Discord workflow first.")
            return 0
        costco = refresh_costco(now, state)
        run(now, WebhookClient(api, webhooks), items, windows, events, calendar["undated"], market.get("products", []), state, costco)
    except DiscordError as error:
        print(f"Discord posting stopped: {error}")
        return 1
    finally:
        save_json(state_path, state)
    print("Discord is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
