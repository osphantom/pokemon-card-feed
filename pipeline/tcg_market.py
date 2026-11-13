"""Resale prices for sealed product in other trading card games — Lorcana, One
Piece, Magic, Star Wars Unlimited, and more — from the same TCGplayer data
(TCGCSV) the Pokémon prices use.

No MSRP rules and no eBay cross-check here: just what each sealed product currently
sells for, keyed by name, so a non-Pokémon preorder or in-store drop can be judged
against its resale price. The buy price comes from the store or the listing.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

import requests

from .common import CONFIG_DIR, DATA_DIR, EASTERN, USER_AGENT, iso, load_json, parse_iso, save_json

TCGCSV = "https://tcgcsv.com/tcgplayer"
REFRESH_AFTER = timedelta(hours=11)


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
