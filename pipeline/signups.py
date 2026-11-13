"""Turn raffle and preorder news into priced, profitable sign-ups.

The news feed names opportunities ("Walmart lottery for 30th Celebration UPCs",
"Delta Reign preorders live at Amazon") but as raw headlines. This matches each
one to a resale row, prices it at the normal store price (what you'd pay if you
win the draw or grab the preorder), and keeps only the ones that clear the bar —
so the raffles and preorders channels show opportunities to act on, not reading.
"""
from __future__ import annotations

from datetime import timedelta

from .common import CONFIG_DIR, DATA_DIR, load_json, parse_iso
from .market import title_matches
from .profit import estimate

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
    }


def is_fresh(item: dict, now) -> bool:
    try:
        return now - parse_iso(item["published"]) <= timedelta(days=FRESH_DAYS)
    except (KeyError, ValueError):
        return False


def collect(items: list[dict], products: list[dict], programs: list[dict], now, costs: dict | None = None,
            min_profit: int = MIN_PROFIT) -> list[dict]:
    """Profitable raffle and preorder sign-ups, best profit first, one per product+store."""
    costs = costs if costs is not None else load_json(CONFIG_DIR / "assumptions.json")
    by_store = {program["store"]: program for program in programs}
    # One entry per product+kind. When several articles cover it, keep the one with a named store
    # (so "enter at Walmart" beats "see the article"), then the higher profit.
    best: dict = {}
    for item in items:
        if item.get("category") not in SIGNUP_CATEGORIES or not is_fresh(item, now):
            continue
        signup = priced_signup(item, products, costs, by_store)
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
