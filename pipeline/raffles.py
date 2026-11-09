"""Raffles, drawings, and invites that anyone can sign up for."""
from __future__ import annotations

from datetime import date

from .common import CONFIG_DIR, DATA_DIR, EASTERN, load_json
from .profit import estimate

# What a sign-up has to clear after eBay fees, shipping, and tax to be worth showing.
MIN_PROFIT = 20


def priced_item(item: dict, rows: dict, costs: dict) -> dict:
    row = rows.get(item["match"])
    if row is None:
        return {**item, "market": None, "profit": None}
    return {**item, "market": row["market"], "profit": estimate(row["market"], item["price"], row["ship"], costs)["profit"]}


def load_current(now, products: list[dict]) -> dict:
    """Return the sign-up programs, plus open sign-ups priced from the feed's resale data."""
    data = load_json(DATA_DIR / "raffles.json", {"programs": [], "open": []})
    today = now.astimezone(EASTERN).date()
    costs = load_json(CONFIG_DIR / "assumptions.json")
    rows = {product["name"]: product for product in products}
    still_open = [item for item in data["open"] if not item["closes"] or date.fromisoformat(item["closes"]) >= today]
    return {"programs": data["programs"], "open": [priced_item(item, rows, costs) for item in still_open]}


def worth_showing(item: dict) -> bool:
    """An item with no resale data stays listed, because it may still be worth a lot."""
    return item["profit"] is None or item["profit"] >= MIN_PROFIT
