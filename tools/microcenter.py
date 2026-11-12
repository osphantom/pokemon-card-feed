"""Local Micro Center Pokémon tracker.

Reads ONE Micro Center store's catalog through the `mcenter` CLI
(https://github.com/nlwstein/microcenter-cli). `mcenter` gets its data through a
session YOU establish yourself by solving Micro Center's one-time browser check
(`mcenter session interactive`) -- nothing here automates around that check.
Micro Center shows a Cloudflare bot check to plain requests, which is exactly why
the session has to be yours. This runs on YOUR computer on a schedule you set up;
it can't run in GitHub Actions because there's no browser there to hold the session.

What it does: searches your store for sealed English Pokémon TCG, prices each hit
against the feed's resale data (data/market.json), and posts in-stock deals to a
Discord webhook, plus a quiet note when something new shows up (a release landing).

One-time setup (and again whenever the session expires):
    pip install microcenter-cli
    mcenter session interactive          # opens your browser; you solve the check
    mcenter stores find <your city>      # confirm the store id (Parkville MD = 125)
Point it at a channel once (PowerShell):
    setx MICROCENTER_WEBHOOK "https://discord.com/api/webhooks/..."
Then run (from the repo root):
    python -m tools.microcenter            # add --dry to print without posting
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline.common import CONFIG_DIR, DATA_DIR, fold, iso, load_json, save_json
from pipeline.profit import estimate

STATE_PATH = DATA_DIR / "microcenter_state.json"
PRODUCT_URL = "https://www.microcenter.com/product/{}/"
SEARCH_TIMEOUT = 90

# Mirrors the watcher's sealed test: a Pokémon sealed product, not a single/graded/accessory.
SEALED = re.compile(r"elite trainer|booster|blister|\btin\b|collection|bundle|battle deck|build ?& ?battle|premium|\bbox\b|\bpacks?\b", re.I)
NOT_SEALED = re.compile(
    r"sleeves?|binder(?! collection| &| and)|deck box|deck case|play ?mat|portfolio|album|top ?loader|"
    r"plush|figure(?! collection)|graded|\bpsa\b|\bbgs\b|\bcgc\b|acrylic|\bcase\b|#\s?\d+|\d+/\d+|"
    r"near mint|japanese|korean|chinese|lot of|\bbulk\b|single card|dice|sleeve|binder\b",
    re.I,
)
OUT_WORDS = ("sold out", "out of stock", "unavailable", "not available", "coming soon", "notify me")
IN_WORDS = ("in stock", "in-store", "limited stock", "available")
# Words that only describe the kind of product, so the resale match keys off the set/character instead.
FORMAT_WORDS = set(
    "pokemon pokémon tcg trading card game the and of exclusive international retail version single pack packs "
    "blister checklane premium elite trainer box booster bundle tin tins mini collection build battle ex "
    "scarlet violet sword shield sv swsh me series".split()
)


class McenterUnavailable(Exception):
    """mcenter isn't installed or has no live session."""


def significant(name: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", fold(name)) if word not in FORMAT_WORDS and len(word) > 1]


def looks_sealed(name: str) -> bool:
    folded = fold(name)
    return "pok" in folded and bool(SEALED.search(folded)) and not NOT_SEALED.search(folded)


def stock_of(stock_text: str | None) -> str:
    text = (stock_text or "").lower()
    if any(word in text for word in OUT_WORDS):
        return "out"
    if any(word in text for word in IN_WORDS):
        return "in"
    return "unknown"


def build_book(market: dict, assumptions: dict) -> dict:
    rows = [
        {"name": p["name"], "market": p["market"], "msrp": p["msrp"], "ship": p["ship"], "sig": significant(p["name"])}
        for p in market.get("products", [])
        if p.get("market") and p.get("ship")
    ]
    return {"rows": rows, "assumptions": assumptions}


def value_of(name: str, price: float | None, book: dict) -> dict | None:
    """Best resale match for a Micro Center item name, and the profit at its price."""
    words = set(significant(name))
    fits = [row for row in book["rows"] if row["sig"] and set(row["sig"]) <= words]
    if not fits:
        return None
    longest = max(len(row["sig"]) for row in fits)
    # Most specific match wins; on a tie, the cheapest so profit is never overstated.
    row = min((row for row in fits if len(row["sig"]) == longest), key=lambda row: row["market"])
    buy = price if price else row["msrp"]
    result = estimate(row["market"], buy, row["ship"], book["assumptions"])
    return {"match": row["name"], "market": row["market"], "profit": result["profit"]}


def enrich(item: dict, book: dict) -> dict:
    return {
        "id": item["product_id"],
        "name": item["name"],
        "price": item.get("price"),
        "stock": stock_of(item.get("stock_text")),
        "stock_text": item.get("stock_text"),
        "url": PRODUCT_URL.format(item["product_id"]),
        "value": value_of(item["name"], item.get("price"), book),
    }


def pays(item: dict, min_profit: float) -> bool:
    return bool(item["value"] and item["value"]["profit"] >= min_profit)


def scan(raw_items: list[dict], book: dict, min_profit: float, previous: dict | None):
    """Sort Micro Center hits into profitable in-stock deals, brand-new listings, and the
    new state to save. The first run (no previous state) only records, so it doesn't spam."""
    seen = {}
    sealed = [enrich(item, book) for item in raw_items if looks_sealed(item.get("name", ""))]
    # Keep the cheapest listing when the same product id shows up under several searches.
    by_id = {}
    for item in sealed:
        if item["id"] not in by_id or (item["price"] or 1e9) < (by_id[item["id"]]["price"] or 1e9):
            by_id[item["id"]] = item

    deals, arrivals = [], []
    for item in by_id.values():
        seen[item["id"]] = {"stock": item["stock"], "pays": pays(item, min_profit)}
        if previous is None:
            continue
        before = previous.get(item["id"])
        if before is None:
            arrivals.append(item)
            if item["stock"] == "in" and pays(item, min_profit):
                deals.append(item)
        elif item["stock"] == "in" and before.get("stock") != "in" and pays(item, min_profit):
            deals.append(item)
    return deals, arrivals, seen


def deal_message(item: dict, store_name: str) -> dict:
    value = item["value"]
    price = f"${item['price']:.2f}" if item["price"] is not None else "the shelf price"
    return {
        "username": "Micro Center Watch",
        "content": "@everyone",
        "allowed_mentions": {"parse": ["everyone"]},
        "embeds": [{
            "title": f"💰 Micro Center: {item['name']}"[:250],
            "url": item["url"],
            "color": 0x12703F,
            "description": (
                f"In stock for {price} at Micro Center ({store_name}). "
                f"Sells for about ${value['market']:.2f} — **you'd make about +${value['profit']:.2f}** after fees.\n"
                f"**[Open the page →]({item['url']})**"
            ),
        }],
    }


def arrivals_message(items: list[dict], store_name: str) -> dict:
    lines = []
    for item in items[:12]:
        price = f"${item['price']:.2f}" if item["price"] is not None else "—"
        flag = f" · +${item['value']['profit']:.0f} flip" if item["value"] and item["value"]["profit"] >= 20 else ""
        lines.append(f"• [{item['name'][:70]}]({item['url']}) — {price} · {item['stock']}{flag}")
    return {
        "username": "Micro Center Watch",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": f"🆕 New Pokémon at Micro Center ({store_name})",
            "color": 0x8A5A00,
            "description": "\n".join(lines),
            "footer": {"text": "Call the store before you go — stock shown is from their site, not a guarantee."},
        }],
    }


def run_search(query: str, store_id: str) -> list[dict]:
    if shutil.which("mcenter") is None:
        raise McenterUnavailable("The `mcenter` CLI isn't installed (pip install microcenter-cli).")
    try:
        result = subprocess.run(
            ["mcenter", "search", query, "--store", str(store_id), "--sort", "newest", "--per-page", "96", "--json"],
            capture_output=True, text=True, timeout=SEARCH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise McenterUnavailable(f"mcenter search '{query}' timed out")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if "session" in message.lower():
            raise McenterUnavailable("no valid Micro Center session. Run `mcenter session interactive` and solve the check.")
        raise McenterUnavailable(f"mcenter search '{query}' failed: {message[:160]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise McenterUnavailable(f"mcenter returned non-JSON: {result.stdout[:160]!r}")


def gather(config: dict, search=run_search) -> list[dict]:
    store_id = config["store"]["id"]
    by_id = {}
    for query in config["queries"]:
        for item in search(query, store_id):
            by_id.setdefault(item["product_id"], item)
    return list(by_id.values())


def post(webhook: str, message: dict) -> bool:
    return requests.post(webhook, json=message, timeout=30).ok


def run(config: dict, book: dict, previous: dict | None, now, search=run_search, send=None) -> dict:
    raw = gather(config, search)
    deals, arrivals, seen = scan(raw, book, config.get("min_profit", 20), previous)
    store_name = config["store"]["name"]
    if send:
        for item in deals:
            send(deal_message(item, store_name))
        if arrivals:
            send(arrivals_message(arrivals, store_name))
    return {"checked_at": iso(now), "store": config["store"], "seen": seen, "deals": len(deals), "arrivals": len(arrivals), "found": len(raw)}


def main() -> int:
    dry = "--dry" in sys.argv
    webhook = os.environ.get("MICROCENTER_WEBHOOK", "").strip()
    if not webhook and not dry:
        print("Set MICROCENTER_WEBHOOK to a Discord webhook URL, or pass --dry to print without posting.")
        return 2
    config = load_json(CONFIG_DIR / "microcenter.json")
    market = load_json(DATA_DIR / "market.json", {"products": []})
    assumptions = load_json(CONFIG_DIR / "assumptions.json")
    book = build_book(market, assumptions)
    state = load_json(STATE_PATH)
    previous = state["seen"] if state else None
    send = None if dry else (lambda message: post(webhook, message))
    try:
        summary = run(config, book, previous, datetime.now(timezone.utc), send=send)
    except McenterUnavailable as error:
        print(f"Micro Center check stopped: {error}")
        return 1
    save_json(STATE_PATH, {"checked_at": summary["checked_at"], "seen": summary["seen"]})
    note = "first run — recorded, nothing posted" if previous is None else f"{summary['deals']} deal(s), {summary['arrivals']} new"
    print(f"Micro Center {config['store']['name']}: {summary['found']} items found, {len(summary['seen'])} sealed. {note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
