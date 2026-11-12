"""Resale prices for Pokémon accessories (plush, peripherals, toys), kept as a
self-refreshing list so the stock watcher never has to call eBay itself.

Each run asks eBay what a set of Pokémon accessory searches is selling for, groups
the listings into distinct items by their significant words, and appends today's
price to a rolling 30-day history per item. The watcher reads the published list
and matches whatever is in stock against it. web/assets and the watcher's logic.js
mirror the token rule in `tokens()` so both sides recognize the same item.
"""
from __future__ import annotations

import re
import statistics
import time
from datetime import date, datetime, timedelta

import requests

from .common import CONFIG_DIR, DATA_DIR, EASTERN, USER_AGENT, fold, iso, load_json, parse_iso, save_json
from .market import EbayClient
from .profit import estimate

REFRESH_AFTER = timedelta(hours=11)
EBAY_REFUSALS_BEFORE_STOP = 5
WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str, stopwords: set[str]) -> list[str]:
    """The significant words of a title: folded, no stopwords, no bare numbers.

    Mirrored in the watcher's logic.js so a store title and a catalog item reduce
    to the same words."""
    out = []
    # Drop apostrophes before splitting so "trainer's" folds to one word, matching the watcher.
    for word in WORD.findall(fold(text).replace("'", "")):
        if word in stopwords or len(word) < 2 or word.isdigit():
            continue
        if word not in out:
            out.append(word)
    return out


def signature(token_list: list[str]) -> str:
    return "|".join(sorted(token_list))


def ship_for(token_list: list[str], ship_map: dict, fallback: str) -> str:
    for word in token_list:
        if word in ship_map:
            return ship_map[word]
    return fallback or ship_map.get("default", "pack")


def listing_total(listing: dict) -> float | None:
    """Item price plus the cheapest shown shipping, or None if unreadable."""
    try:
        price = float(listing["price"]["value"])
    except (KeyError, TypeError, ValueError):
        return None
    ship = 0.0
    options = listing.get("shippingOptions") or []
    if options:
        try:
            ship = float(options[0]["shippingCost"]["value"])
        except (KeyError, TypeError, ValueError):
            ship = 0.0
    return round(price + ship, 2)


def believable_price(totals: list[float], min_price: float):
    """Median of the five cheapest believable totals, so one odd listing can't set the price."""
    clean = sorted(total for total in totals if total is not None and total >= min_price)
    if not clean:
        return None, 0
    return round(statistics.median(clean[:5]), 2), len(clean)


def group_listings(listings: list[dict], stopwords: set[str], exclude: list, ship_map: dict, default_ship: str) -> dict:
    """Cluster one search's listings into {signature: {tokens, title, totals, ship}}."""
    groups: dict[str, dict] = {}
    for listing in listings:
        title = listing.get("title", "")
        folded = fold(title)
        if any(pattern.search(folded) for pattern in exclude):
            continue
        token_list = tokens(title, stopwords)
        # One word is just a category ("plush"); it would match far too much in a store.
        if len(token_list) < 2:
            continue
        total = listing_total(listing)
        if total is None:
            continue
        key = signature(token_list)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "tokens": token_list,
                "title": title.strip(),
                "ship": ship_for(token_list, ship_map, default_ship),
                "totals": [],
            }
        # Keep the shortest title as the cleanest name for the item.
        if len(title.strip()) < len(group["title"]):
            group["title"] = title.strip()
        group["totals"].append(total)
    return groups


def items_from_groups(groups: dict, min_listings: int, min_price: float, today: date) -> list[dict]:
    items = []
    for key, group in groups.items():
        if len(group["totals"]) < min_listings:
            continue
        price, count = believable_price(group["totals"], min_price)
        if price is None:
            continue
        items.append({
            "key": key,
            "tokens": group["tokens"],
            "title": group["title"],
            "ship": group["ship"],
            "price": price,
            "count": count,
            "date": today.isoformat(),
        })
    return items


def price_catalog_item(client: EbayClient, item: dict, stopwords: set[str], exclude: list, min_price: float,
                       ship_map: dict, today: date) -> dict | None:
    """A named item we always want priced: search its query, keep only listings that
    actually contain its words, and take the believable price."""
    listings = client.search(item["query"], sort="price")
    if not listings:
        return None
    wanted = set(tokens(item["name"], stopwords))
    totals = []
    for listing in listings:
        folded = fold(listing.get("title", ""))
        if any(pattern.search(folded) for pattern in exclude):
            continue
        if not wanted <= set(tokens(listing.get("title", ""), stopwords)):
            continue
        total = listing_total(listing)
        if total is not None:
            totals.append(total)
    price, count = believable_price(totals, min_price)
    if price is None:
        return None
    token_list = tokens(item["name"], stopwords)
    return {
        "key": signature(token_list),
        "tokens": token_list,
        "title": item["name"],
        "ship": item.get("ship") or ship_for(token_list, ship_map, "pack"),
        "price": price,
        "count": count,
        "date": today.isoformat(),
    }


def merge_history(existing: list[dict], fresh: list[dict], today: date, history_days: int) -> list[dict]:
    """Fold today's prices into the rolling per-item history and drop stale items.

    The published `market` for each item is the median of its last `history_days`
    of daily prices, so a single bad reading never drives an alert."""
    cutoff = today - timedelta(days=history_days)
    by_key = {item["key"]: dict(item) for item in existing}

    for item in fresh:
        snap = {"date": item["date"], "price": item["price"], "count": item["count"]}
        record = by_key.get(item["key"])
        if record is None:
            by_key[item["key"]] = {
                "key": item["key"],
                "tokens": item["tokens"],
                "title": item["title"],
                "ship": item["ship"],
                "history": [snap],
            }
        else:
            # Refresh the name/shipping to the latest read, keep the history.
            record["tokens"] = item["tokens"]
            record["title"] = item["title"]
            record["ship"] = item["ship"]
            history = [h for h in record.get("history", []) if h["date"] != item["date"]]
            history.append(snap)
            record["history"] = history

    result = []
    for record in by_key.values():
        history = [h for h in record.get("history", []) if date.fromisoformat(h["date"]) >= cutoff]
        if not history:
            continue
        history.sort(key=lambda h: h["date"])
        record["history"] = history[-history_days:]
        record["market"] = round(statistics.median(h["price"] for h in record["history"]), 2)
        record["count"] = record["history"][-1]["count"]
        record["last_seen"] = record["history"][-1]["date"]
        result.append(record)

    # Most recently seen first, then priciest, and cap the list.
    result.sort(key=lambda r: (r["last_seen"], r["market"]), reverse=True)
    return result


def collect_fresh(client: EbayClient, config: dict, today: date) -> list[dict]:
    stopwords = set(config["stopwords"])
    exclude = [re.compile(pattern) for pattern in config["exclude"]]
    ship_map = config["ship_by_keyword"]
    default_ship = ship_map.get("default", "pack")
    min_listings = config["min_listings"]
    min_price = config["min_price"]

    fresh: dict[str, dict] = {}
    refusals = 0

    def remember(item: dict) -> None:
        # If the same item turns up under two searches, keep the one backed by more listings.
        old = fresh.get(item["key"])
        if old is None or item["count"] > old["count"]:
            fresh[item["key"]] = item

    for entry in config.get("catalog", []):
        priced = price_catalog_item(client, entry, stopwords, exclude, min_price, ship_map, today)
        if priced:
            remember(priced)
        time.sleep(0.25)

    for sweep in config.get("sweeps", []):
        listings = client.search(sweep["query"], sort="best", limit=config.get("per_query_listings", 50))
        if listings is None:
            refusals += 1
            if refusals >= EBAY_REFUSALS_BEFORE_STOP:
                print(f"eBay refused {refusals} accessory searches in a row; stopping until the next refresh.")
                break
            time.sleep(0.25)
            continue
        refusals = 0
        groups = group_listings(listings, stopwords, exclude, ship_map, sweep.get("ship", default_ship))
        for item in items_from_groups(groups, min_listings, min_price, today):
            remember(item)
        time.sleep(0.25)

    return list(fresh.values())


def build_accessories(now: datetime, session=None) -> dict:
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    config = load_json(CONFIG_DIR / "accessories.json")
    today = now.astimezone(EASTERN).date()

    current = load_json(DATA_DIR / "accessories.json", {}) or {}
    existing = current.get("items", [])

    client_id, client_secret = _env("EBAY_CLIENT_ID"), _env("EBAY_CLIENT_SECRET")
    client = EbayClient(client_id, client_secret, session) if client_id and client_secret else None
    if client is None or not client.authenticate():
        # No keys or eBay is down: keep the list we have, just prune what's gone stale.
        merged = merge_history(existing, [], today, config["history_days"])
        return _payload(now, config, merged, ebay_used=False)

    fresh = collect_fresh(client, config, today)
    merged = merge_history(existing, fresh, today, config["history_days"])[: config["max_items"]]
    return _payload(now, config, merged, ebay_used=True)


def _payload(now: datetime, config: dict, items: list[dict], ebay_used: bool) -> dict:
    return {
        "generated_at": iso(now),
        "ebay_used": ebay_used,
        "stopwords": config["stopwords"],
        "items": items,
    }


def _env(name: str) -> str:
    import os
    return os.environ.get(name, "").strip()


def refresh_if_due(now: datetime, session=None, force: bool = False) -> dict:
    """Refresh about twice a day, in step with the card prices. Keep the last list if eBay is down."""
    path = DATA_DIR / "accessories.json"
    current = load_json(path)
    if current and current.get("items") and not force and now - parse_iso(current["generated_at"]) < REFRESH_AFTER:
        return current
    try:
        fresh = build_accessories(now, session)
    except (requests.RequestException, ValueError, KeyError) as error:
        print(f"Accessory refresh failed, keeping last list: {type(error).__name__}")
        config = load_json(CONFIG_DIR / "accessories.json")
        return current or {"generated_at": iso(now), "ebay_used": False, "stopwords": config["stopwords"], "items": []}
    save_json(path, fresh)
    return fresh
