#!/usr/bin/env python3
"""Bundle flip calculator — add up several items you bought together (e.g. a Costco
bundle with a few products) and see the TOTAL profit after eBay fees, shipping, and tax.

It reads flip-list.csv (sits next to this file). That sheet already has `net_sale` for
each item — what you keep from one sale after eBay's cut and shipping. So you just tell
it what you bought and what you paid, and it subtracts your cost (plus your sales tax).

USAGE
  Interactive:    python flip_calc.py
  One bundle:     python flip_calc.py --paid 125 "elite trainer box" "booster bundle"
  Per-item price: python flip_calc.py "30th celebration elite trainer box=49.99" "booster bundle=26.94"

Match is by name (any part of the product name in flip-list.csv, case-insensitive).
Change YOUR_SALES_TAX below to your state's rate. Regenerate the sheet anytime with
`python -m tools.flip_sheet` so prices stay current.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

YOUR_SALES_TAX = 0.07  # what YOU pay in tax when buying. Set to your state's rate.
CSV = Path(__file__).resolve().parent / "flip-list.csv"


def load_products():
    with open(CSV, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


PRODUCTS = load_products()


def find(query: str):
    """Best product match for a name, or None."""
    needle = query.lower().strip()
    hits = [row for row in PRODUCTS if needle in row["product"].lower()]
    if not hits:
        return None
    hits.sort(key=lambda row: (len(row["product"]), -float(row["resale"])))
    return hits[0]


def bundle(items, total_paid=None):
    """items: list of (name, paid_or_None). total_paid overrides per-item prices."""
    lines, net, per_item_paid = [], 0.0, 0.0
    for name, paid in items:
        row = find(name)
        if row is None:
            lines.append({"query": name, "matched": None})
            continue
        net_sale = float(row["net_sale"])
        net += net_sale
        if paid is not None:
            per_item_paid += paid
        lines.append({"query": name, "matched": row["product"], "resale": float(row["resale"]),
                      "net_sale": net_sale, "paid": paid})
    paid = total_paid if total_paid is not None else per_item_paid
    cost = paid * (1 + YOUR_SALES_TAX)
    return {
        "lines": lines,
        "total_net_sale": round(net, 2),
        "paid": round(paid, 2),
        "cost_with_tax": round(cost, 2),
        "profit": round(net - cost, 2),
    }


def show(result):
    print()
    for line in result["lines"]:
        if not line.get("matched"):
            print(f"  ?  no match for '{line['query']}' — check the spelling against flip-list.csv")
            continue
        paid = f" (paid ${line['paid']:.2f})" if line.get("paid") is not None else ""
        print(f"  * {line['matched'][:58]}{paid}")
        print(f"      sells ${line['resale']:.2f}  ->  you net ${line['net_sale']:.2f} after eBay fees + shipping")
    tax = int(YOUR_SALES_TAX * 100)
    print(f"\n  You net from selling all of it:  ${result['total_net_sale']:.2f}")
    print(f"  You paid (+{tax}% sales tax):        ${result['cost_with_tax']:.2f}   (${result['paid']:.2f} before tax)")
    verdict = "PROFIT" if result["profit"] >= 0 else "LOSS"
    print(f"  ===> {verdict}:  ${result['profit']:.2f}\n")


def parse(arg: str):
    if "=" in arg:
        name, _, price = arg.rpartition("=")
        try:
            return name.strip(), float(price)
        except ValueError:
            return arg, None
    return arg, None


def main(argv):
    paid, names, i = None, [], 0
    while i < len(argv):
        if argv[i] in ("--paid", "-p"):
            paid = float(argv[i + 1]); i += 2; continue
        names.append(argv[i]); i += 1
    if not names:
        print("Bundle flip calculator. Enter product names one per line; blank line to finish.")
        while True:
            line = input("  item> ").strip()
            if not line:
                break
            names.append(line)
        if paid is None:
            entered = input("  total you paid for all of them  $").strip()
            paid = float(entered) if entered else None
    show(bundle([parse(a) for a in names], paid))


if __name__ == "__main__":
    main(sys.argv[1:])
