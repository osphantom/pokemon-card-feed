"""Generate flip-list.csv — every current product the feed monitors, with resale,
shipping, eBay fees, and the HIGHEST price you can pay and still make money.

Columns (plain English):
  product            - the item
  category           - Pokemon sealed / a trading card game / accessory / toy
  resale             - what it sells for (TCGplayer market or eBay, in $)
  ship_class / ship_cost - how it ships and the cost
  ebay_fees          - eBay's cut on this sale (~14% incl. the tax eBay charges on)
  net_sale           - what you KEEP from one sale after fees + shipping (before what you paid)
  max_buy_breakeven  - pay this or less to not lose money
  max_buy_for_$20    - pay this or less to clear at least $20 profit
  msrp               - the normal retail price, when known
  source             - where resale came from

Run:  python -m tools.flip_sheet   (writes flip-list.csv in the repo root)
"""
from __future__ import annotations

import csv
import re

from pipeline.common import CONFIG_DIR, ROOT, load_json
from pipeline.profit import estimate

ASSUMPTIONS = load_json(CONFIG_DIR / "assumptions.json")
TAX = ASSUMPTIONS["your_tax"]
BOXY = re.compile(r"booster box|booster display|\bdisplay\b|elite trainer|trainer box|ultra-?premium")


def tcg_ship(name: str) -> str:
    low = name.lower()
    if "booster pack" in low and "box" not in low:
        return "pack"
    if "bundle" in low and "box" not in low:
        return "bundle"
    return "box"


def row_for(name, category, resale, ship, msrp="", source=""):
    """One sheet row. `net_sale` and the max-buy prices don't depend on what you paid."""
    est = estimate(resale, 0.0, ship, ASSUMPTIONS)  # buy=0 so profit == proceeds
    net_sale = est["proceeds"]                        # resale - fees - shipping - supplies
    return {
        "product": name,
        "category": category,
        "resale": round(resale, 2),
        "ship_class": ship,
        "ship_cost": ASSUMPTIONS["shipping"][ship],
        "ebay_fees": est["fees"],
        "net_sale": round(net_sale, 2),
        "max_buy_breakeven": round(net_sale / (1 + TAX), 2),
        "max_buy_for_$20": round((net_sale - 20) / (1 + TAX), 2),
        "msrp": msrp,
        "source": source,
    }


def collect():
    rows = []
    # Pokemon sealed (data/market.json) — current sets only
    for p in load_json(ROOT / "data" / "market.json", {"products": []})["products"]:
        if p.get("current") and p.get("market"):
            rows.append(row_for(p["name"], "Pokemon sealed", p["market"], p["ship"],
                                msrp=p.get("msrp", ""), source="TCGplayer+eBay"))
    # Other trading card games (data/tcg_market.json) — eBay-verified only
    for p in load_json(ROOT / "data" / "tcg_market.json", {"products": []})["products"]:
        if p.get("verified") and p.get("current") and p.get("market"):
            rows.append(row_for(p["name"], p["game"], p["market"], tcg_ship(p["name"]), source="eBay (verified)"))
    # Accessories and toys (data/accessories.json)
    for p in load_json(ROOT / "data" / "accessories.json", {"items": []}).get("items", []):
        if p.get("market"):
            rows.append(row_for(p["title"], "accessory/toy", p["market"], p.get("ship", "pack"), source="eBay"))
    # Best profit headroom first.
    rows.sort(key=lambda r: r["max_buy_for_$20"] - 0, reverse=True)
    return rows


def main():
    rows = collect()
    fields = ["product", "category", "resale", "ship_class", "ship_cost", "ebay_fees",
              "net_sale", "max_buy_breakeven", "max_buy_for_$20", "msrp", "source"]
    out = ROOT / "flip-list.csv"
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out} with {len(rows)} products.")
    print(f"Cost model: eBay {ASSUMPTIONS['fee_rate']*100:.2f}% + ${ASSUMPTIONS['per_order_large']}/order, "
          f"buyer tax {ASSUMPTIONS['buyer_tax']*100:.0f}% (eBay fees it), your sales tax {TAX*100:.0f}%, "
          f"${ASSUMPTIONS['supplies']} supplies per order.")


if __name__ == "__main__":
    main()
