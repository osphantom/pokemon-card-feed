"""Turn raffle and preorder news into priced, profitable sign-ups.

The news feed names opportunities ("Walmart lottery for 30th Celebration UPCs",
"Delta Reign preorders live at Amazon") but as raw headlines. This matches each
one to a resale row, prices it at the normal store price (what you'd pay if you
win the draw or grab the preorder), and keeps only the ones that clear the bar —
so the raffles and preorders channels show opportunities to act on, not reading.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .common import CONFIG_DIR, DATA_DIR, fold, load_json, parse_iso
from .market import title_matches
from .profit import estimate

PRICE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
BOXY = re.compile(r"booster box|booster display|\bdisplay\b|elite trainer|trainer box|ultra-?premium")

# Same bar as everywhere else: what a sign-up must clear after eBay fees, shipping, and tax.
MIN_PROFIT = 20
# Raffles close and preorders sell through, so old articles aren't opportunities any more.
FRESH_DAYS = 10
SIGNUP_CATEGORIES = ("raffles", "preorders")


def match_product(title: str, products: list[dict]) -> dict | None:
    """The resale row a headline is about, if any. Cheapest fitting version, so profit is never overstated."""
    matches = [
        product
        for product in products
        if product.get("msrp") and product.get("market") and title_matches(product["name"], title)
    ]
    if not matches:
        return None
    return min(matches, key=lambda product: product["market"])


def priced_signup(item: dict, products: list[dict], costs: dict, programs_by_store: dict) -> dict | None:
    product = match_product(item["title"], products)
    if product is None:
        return None
    profit = estimate(product["market"], product["msrp"], product["ship"], costs)["profit"]
    store = item["stores"][0] if item.get("stores") else None
    program = programs_by_store.get(store) if store else None
    return {
        "kind": item["category"],
        "store": store or "See the article",
        "product": product["name"],
        "market": product["market"],
        "msrp": product["msrp"],
        "profit": profit,
        "title": item["title"],
        "link": item["link"],
        "how": program["how"] if program else None,
        "published": item["published"],
        "game": "Pokémon",
    }


def headline_price(title: str):
    """The dollar amount named in a headline, if any — the buy price for a non-Pokémon preorder."""
    match = PRICE.search(title)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value if value >= 5 else None


def tcg_ship(name: str) -> str:
    low = name.lower()
    if "booster pack" in low and "box" not in low:
        return "pack"
    if "bundle" in low and "box" not in low:
        return "bundle"
    return "box"


def match_tcg(title: str, tcg_products: list[dict]) -> dict | None:
    """Match a non-Pokémon headline to a set by its name, then take that set's main sealed box."""
    folded = fold(title)
    by_set: dict = {}
    for product in tcg_products:
        # Only prices eBay actually confirmed — never an unverified catalog price.
        if product.get("market") and product.get("verified"):
            by_set.setdefault(product["set"], []).append(product)
    best, best_len = None, 0
    for set_name, products in by_set.items():
        folded_set = fold(set_name)
        if len(folded_set) >= 6 and folded_set in folded and len(folded_set) > best_len:
            best, best_len = products, len(folded_set)
    if not best:
        return None
    boxes = [product for product in best if BOXY.search(product["name"].lower())]
    return max(boxes or best, key=lambda product: product["market"])


def priced_tcg_signup(item: dict, tcg_products: list[dict], costs: dict) -> dict | None:
    price = headline_price(item["title"])
    if price is None:
        return None
    product = match_tcg(item["title"], tcg_products)
    if product is None:
        return None
    profit = estimate(product["market"], price, tcg_ship(product["name"]), costs)["profit"]
    store = item["stores"][0] if item.get("stores") else None
    return {
        "kind": item["category"],
        "store": store or "See the article",
        "product": f"{product['game']}: {product['name']}",
        "market": product["market"],
        "msrp": price,
        "profit": profit,
        "title": item["title"],
        "link": item["link"],
        "how": None,
        "published": item["published"],
        "game": product["game"],
    }


def is_fresh(item: dict, now) -> bool:
    try:
        return now - parse_iso(item["published"]) <= timedelta(days=FRESH_DAYS)
    except (KeyError, ValueError):
        return False


def collect(items: list[dict], products: list[dict], programs: list[dict], now, costs: dict | None = None,
            min_profit: int = MIN_PROFIT, tcg_products: list[dict] | None = None) -> list[dict]:
    """Profitable raffle and preorder sign-ups, best profit first, one per product+store.

    Pokémon items price against the Pokémon table at list price; non-Pokémon items price against
    the other-games resale table using the price named in the headline."""
    costs = costs if costs is not None else load_json(CONFIG_DIR / "assumptions.json")
    by_store = {program["store"]: program for program in programs}
    tcg_products = tcg_products or []
    # One entry per product+kind. When several articles cover it, keep the one with a named store
    # (so "enter at Walmart" beats "see the article"), then the higher profit.
    best: dict = {}
    for item in items:
        if item.get("category") not in SIGNUP_CATEGORIES or not is_fresh(item, now):
            continue
        signup = priced_signup(item, products, costs, by_store)
        if signup is None and tcg_products:
            signup = priced_tcg_signup(item, tcg_products, costs)
        if signup is None or signup["profit"] < min_profit:
            continue
        key = (signup["product"], signup["kind"])
        current = best.get(key)
        if current is None or _better(signup, current):
            best[key] = signup
    return sorted(best.values(), key=lambda signup: -signup["profit"])


def _has_store(signup: dict) -> bool:
    return signup["store"] != "See the article"


def _better(candidate: dict, current: dict) -> bool:
    if _has_store(candidate) != _has_store(current):
        return _has_store(candidate)
    return candidate["profit"] > current["profit"]


def as_open_raffles(signups: list[dict]) -> list[dict]:
    """Shape raffle-kind sign-ups like the curated raffles.json 'open' entries, so the raffles board shows them."""
    opens = []
    for signup in signups:
        if signup["kind"] != "raffles":
            continue
        opens.append({
            "id": f"signup:{signup['store']}:{signup['product']}",
            "store": signup["store"],
            "title": signup["product"],
            "price": signup["msrp"],
            "closes": "",
            "link": signup["link"],
            "market": signup["market"],
            "profit": signup["profit"],
        })
    return opens


def preorder_signups(signups: list[dict]) -> list[dict]:
    return [signup for signup in signups if signup["kind"] == "preorders"]


def load_and_collect(now, products: list[dict], programs: list[dict]) -> list[dict]:
    items = load_json(DATA_DIR / "items.json", {"items": []})["items"]
    return collect(items, products, programs, now)
