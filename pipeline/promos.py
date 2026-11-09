"""Free promo cards: which ones are running and what each one sells for."""
from __future__ import annotations

import re
import time
from datetime import date, timedelta

import requests

from .common import CONFIG_DIR, DATA_DIR, EASTERN, USER_AGENT, iso, load_json, parse_iso
from .market import TCGCSV
from .profit import estimate

REFRESH_AFTER = timedelta(hours=6)
# Cards worth this much ship with tracking, which costs more than a plain envelope.
TRACKED_FROM = 20
# A free card still has to be worth selling.
MIN_KEEP = 1
OK = 200


def load_active(today: date) -> list[dict]:
    promos = load_json(DATA_DIR / "promos.json", {"promos": []})["promos"]
    return [promo for promo in promos if not promo["end"] or date.fromisoformat(promo["end"]) >= today]


def fetch_results(session, url: str):
    """Return a TCGCSV result list, or None so a refusing site isn't asked again."""
    try:
        response = session.get(url, timeout=30)
    except requests.RequestException as error:
        print(f"Promo price check stopped: {type(error).__name__}")
        return None
    if response.status_code != OK:
        print(f"Promo price check stopped: the site returned {response.status_code}")
        return None
    return response.json()["results"]


def fetch_markets(promos: list[dict], session=None, pause: float = 1.0) -> dict:
    """Return {promo id: TCGplayer market price} for the promos TCGplayer has listed."""
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    by_group: dict[int, list[dict]] = {}
    for promo in promos:
        if promo.get("group") and promo.get("match"):
            by_group.setdefault(promo["group"], []).append(promo)

    found = {}
    for group, members in by_group.items():
        products = fetch_results(session, f"{TCGCSV}/{group}/products")
        time.sleep(pause)
        prices = fetch_results(session, f"{TCGCSV}/{group}/prices")
        if products is None or prices is None:
            break
        market_by_id: dict[int, float] = {}
        for row in prices:
            if row.get("marketPrice"):
                market_by_id[row["productId"]] = max(market_by_id.get(row["productId"], 0), row["marketPrice"])
        for promo in members:
            pattern = re.compile(promo["match"], re.I)
            # Staff-stamped versions aren't what stores hand out.
            values = [
                market_by_id[product["productId"]]
                for product in products
                if pattern.search(product["name"]) and "[Staff]" not in product["name"] and product["productId"] in market_by_id
            ]
            if values:
                found[promo["id"]] = min(values)
        time.sleep(pause)
    return found


def keep_after_selling(market: float, costs: dict) -> float:
    """What's left of a free card's sale price after eBay fees and shipping."""
    ship = "card_tracked" if market >= TRACKED_FROM else "card"
    return estimate(market, 0, ship, costs)["profit"]


def priced(promo: dict, market, costs: dict, today: date) -> dict:
    return {
        **promo,
        "market": market,
        "keep": keep_after_selling(market, costs) if market is not None else None,
        "upcoming": date.fromisoformat(promo["start"]) > today,
    }


def worth_showing(promo: dict) -> bool:
    """A promo with no price yet stays listed, because it may still be worth a lot."""
    return promo["keep"] is None or promo["keep"] >= MIN_KEEP


def refresh_if_due(now, state: dict, session=None, pause: float = 1.0) -> list[dict]:
    """Return active promos with prices. Prices are looked up a few times a day."""
    today = now.astimezone(EASTERN).date()
    promos = load_active(today)
    last = state.get("promos_checked_at")
    if not last or now - parse_iso(last) >= REFRESH_AFTER:
        state["promos_checked_at"] = iso(now)
        state["promo_markets"] = {**state.get("promo_markets", {}), **fetch_markets(promos, session, pause)}
    costs = load_json(CONFIG_DIR / "assumptions.json")
    markets = state.get("promo_markets", {})
    return [priced(promo, markets.get(promo["id"]), costs, today) for promo in promos]
