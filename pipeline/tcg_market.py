"""Resale prices for sealed product in other trading card games — Lorcana, One
Piece, Magic, Star Wars Unlimited, and more — from the same TCGplayer data
(TCGCSV) the Pokémon prices use.

No MSRP rules and no eBay cross-check here: just what each sealed product currently
sells for, keyed by name, so a non-Pokémon preorder or in-store drop can be judged
against its resale price. The buy price comes from the store or the listing.
"""
from __future__ import annotations

import os
import re
import statistics
import time
from datetime import date, datetime, timedelta

import requests

from .common import CONFIG_DIR, DATA_DIR, EASTERN, USER_AGENT, fold, iso, load_json, parse_iso, save_json
from .market import EbayClient

TCGCSV = "https://tcgcsv.com/tcgplayer"
REFRESH_AFTER = timedelta(hours=11)
EBAY_REFUSALS_BEFORE_STOP = 5
# Words that don't tell two products apart, so they aren't required to match an eBay listing.
GENERIC = {
    "booster", "box", "boxes", "display", "pack", "packs", "sealed", "collection", "bundle", "starter",
    "deck", "decks", "set", "sets", "edition", "tcg", "trading", "card", "cards", "game", "the", "of",
    "and", "a", "an", "volume", "vol", "english", "new", "double", "elite", "trainer", "premium",
}
BOX_FORM = re.compile(r"booster box|booster display|\bdisplay\b|\bbox\b|elite trainer|trainer box|ultra-?premium")
LISTING_BOX = re.compile(r"\bbox\b|display|elite trainer|ultra-?premium|\bupc\b")
# Bulk listings cost many times a single box, so they must never set the price of one.
LISTING_BULK = re.compile(r"\bcase\b|\blot\b|\bbundle\b|sealed case|booster case|\bbreak\b|\d+\s*(?:boxes|displays)\b|\bx\s*\d+\b|\b\d+\s*x\b|\bqty\b")
WORD = re.compile(r"[a-z0-9]+")


def is_single(product: dict) -> bool:
    """Single cards carry a collector Number in their extended data; sealed product doesn't."""
    return any(entry.get("name") == "Number" for entry in (product.get("extendedData") or []))


def release_date(product: dict):
    released = (product.get("presaleInfo") or {}).get("releasedOn")
    return released[:10] if released else None


def sealed_rows(products: list[dict], prices: dict, group_name: str, label: str,
                sealed: re.Pattern, exclude: re.Pattern, min_market: float, current_days: int, today: date) -> list[dict]:
    rows = []
    for product in products:
        name = product["name"]
        folded = name.lower()
        if is_single(product) or not sealed.search(folded) or exclude.search(folded):
            continue
        market = prices.get(product["productId"], {}).get("marketPrice")
        if not market or market < min_market:
            continue
        release = release_date(product)
        presale = bool((product.get("presaleInfo") or {}).get("isPresale"))
        days_out = (today - date.fromisoformat(release)).days if release else 9999
        rows.append({
            "game": label,
            "name": name,
            "set": group_name,
            "market": round(market, 2),
            "release": release,
            "presale": presale,
            "current": presale or days_out <= current_days,
            "url": product.get("url") or "",
        })
    return rows


def game_rows(session, game: dict, config: dict, today: date) -> list[dict]:
    sealed = re.compile(config["sealed"])
    exclude = re.compile(config["exclude"])
    groups = session.get(f"{TCGCSV}/{game['id']}/groups", timeout=30).json()["results"]
    groups = sorted(groups, key=lambda group: group["groupId"], reverse=True)[: config["recent_groups_per_game"]]
    rows = []
    for group in groups:
        gid = group["groupId"]
        products = session.get(f"{TCGCSV}/{game['id']}/{gid}/products", timeout=30).json()["results"]
        prices = {p["productId"]: p for p in session.get(f"{TCGCSV}/{game['id']}/{gid}/prices", timeout=30).json()["results"]}
        rows.extend(sealed_rows(products, prices, group["name"], game["label"], sealed, exclude,
                                config["min_market"], config["current_days"], today))
        time.sleep(0.2)
    return rows


def distinctive(name: str) -> set:
    """The words that tell this product apart from others — its set, not 'booster box'."""
    return {word for word in WORD.findall(fold(name)) if word not in GENERIC and not word.isdigit() and len(word) > 1}


def listing_total(listing: dict):
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


def believable_ask(name: str, listings: list[dict], min_price: float):
    """Median of the cheapest believable like-for-like listings — what the item actually sells for.

    Listings must share the product's distinctive words, and a box must be priced off box listings,
    never single packs."""
    needed = distinctive(name)
    want_box = bool(BOX_FORM.search(name.lower()))
    totals = []
    for listing in listings:
        title = fold(listing.get("title", ""))
        words = set(WORD.findall(title))
        if needed and not needed <= words:
            continue
        if LISTING_BULK.search(title):
            continue
        if want_box and not LISTING_BOX.search(title):
            continue
        total = listing_total(listing)
        if total is not None and total >= min_price:
            totals.append(total)
    totals.sort()
    if len(totals) < 3:
        return None, len(totals)
    return round(statistics.median(totals[:5]), 2), len(totals)


def ground_with_ebay(rows: list[dict], session, config: dict) -> None:
    """Replace each product's catalog price with what eBay actually asks for it. A product eBay can't
    confirm (too few believable listings) is marked unverified and is never treated as a flip."""
    for row in rows:
        row["catalog"] = row["market"]
        row["verified"] = False
        row["count"] = 0
    client_id = os.environ.get("EBAY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return
    client = EbayClient(client_id, client_secret, session)
    if not client.authenticate():
        return
    refusals = 0
    for row in rows[: config["ebay_lookups"]]:
        listings = client.search(f"{row['name']} sealed", sort="price")
        if listings is None:
            refusals += 1
            if refusals >= EBAY_REFUSALS_BEFORE_STOP:
                print("eBay refused several TCG price checks in a row; stopping.")
                break
            time.sleep(0.25)
            continue
        refusals = 0
        ask, count = believable_ask(row["name"], listings, config["min_market"])
        row["count"] = count
        if ask and count >= config["min_ebay_count"]:
            # The real selling price, never the inflated catalog price.
            row["market"] = ask
            row["verified"] = True
        time.sleep(0.25)


def build(now: datetime, session=None) -> dict:
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    config = load_json(CONFIG_DIR / "tcg.json")
    today = now.astimezone(EASTERN).date()
    rows = []
    for game in config["games"]:
        try:
            rows.extend(game_rows(session, game, config, today))
        except (requests.RequestException, ValueError, KeyError) as error:
            print(f"{game['label']} prices skipped: {type(error).__name__}")
        time.sleep(0.2)
    # Current and upcoming first, then priciest, and cap the list.
    rows.sort(key=lambda row: (row["current"], row["market"]), reverse=True)
    rows = rows[: config["max_products"]]
    ground_with_ebay(rows, session, config)
    return {"generated_at": iso(now), "games": [game["label"] for game in config["games"]], "products": rows}


def refresh_if_due(now: datetime, session=None, force: bool = False) -> dict:
    """Refresh about twice a day, with the Pokémon prices. Keep the last list if the source is down."""
    path = DATA_DIR / "tcg_market.json"
    current = load_json(path)
    if current and current.get("products") and not force and now - parse_iso(current["generated_at"]) < REFRESH_AFTER:
        return current
    try:
        fresh = build(now, session)
    except (requests.RequestException, ValueError, KeyError) as error:
        print(f"TCG price refresh failed, keeping last list: {type(error).__name__}")
        return current or {"generated_at": iso(now), "games": [], "products": []}
    if not fresh["products"] and current and current.get("products"):
        return current
    save_json(path, fresh)
    return fresh
