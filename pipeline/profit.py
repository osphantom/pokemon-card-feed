"""eBay resale math for sealed product. web/assets/app.js mirrors this calculation."""
from __future__ import annotations


def estimate(market: float, buy_price: float, ship_class: str, assumptions: dict) -> dict:
    """Estimated result of buying at buy_price and selling on eBay with free shipping."""
    sale = market * assumptions["sell_pct"]
    # eBay charges its percentage on item, shipping, and the sales tax the buyer pays.
    total = sale * (1 + assumptions["buyer_tax"])
    percent_fee = (
        min(total, assumptions["fee_cap"]) * assumptions["fee_rate"]
        + max(0.0, total - assumptions["fee_cap"]) * assumptions["over_rate"]
    )
    if assumptions["top_rated"]:
        percent_fee *= 0.9
    per_order = (
        assumptions["per_order_small"] if sale <= assumptions["per_order_cutoff"] else assumptions["per_order_large"]
    )
    fees = percent_fee + per_order
    shipping = assumptions["shipping"][ship_class] + assumptions["supplies"]
    proceeds = sale - fees - shipping
    basis = buy_price * (1 - assumptions["retail_discount"]) * (1 + assumptions["your_tax"])
    profit = proceeds - basis
    return {
        "fees": round(fees, 2),
        "shipping": round(shipping, 2),
        "proceeds": round(proceeds, 2),
        "basis": round(basis, 2),
        "profit": round(profit, 2),
        "roi": round(profit / basis, 4) if basis else None,
    }
