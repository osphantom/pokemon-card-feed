"""Sam's Club order counts from PokeRestock's public club pages.

PokeRestock's robots rules allow these pages and it publishes no terms against reading
them. The counts come from dated sheets of reported orders, not live shelf stock. Like the
Costco counts, they go only to the private Discord server with a link back, and aren't
stored in this public repo. Sam's uses the same states as Costco, from config/costco.json.
"""
from __future__ import annotations

import html
import re
import time
from datetime import timedelta

import requests

from .common import CONFIG_DIR, USER_AGENT, iso, load_json, parse_iso

SITE = "https://pokerestockapp.com"
SITEMAP_URL = f"{SITE}/sitemap.xml"
LOOK_EVERY = timedelta(minutes=30)
# Every Sam's item PokeRestock tracks gets its own page, so new items are picked up on their own.
ITEM_PAGE = re.compile(r"<loc>(https://pokerestockapp\.com/sams-club/[a-z0-9-]+/)</loc>")
STATE_BLOCK = re.compile(r'<details class="sc-state" id="st-(?P<slug>[a-z-]+)">(?P<body>.*?)</details>', re.S)
ROW = re.compile(r'<li class="sc-row">(?P<row>.*?)</li>', re.S)
NAME = re.compile(r'<span class="nm">([^<]+)</span>')
CLUB = re.compile(r"Club #(\d+)")
QUANTITY = re.compile(r'<span class="sc-q k-([a-z_]+)[^"]*"><b>([\d,]+)</b>')
SHEET = re.compile(r"Sheet date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
PRODUCT = re.compile(r"counts for the (.+?) \(Sam.s Club item #(\d+)\)")
KINDS = {"on_hand_qty": "hand", "on_order_qty": "order", "in_transit_qty": "transit"}
MAX_ITEMS = 5
OK = 200


def count(text: str) -> int:
    return int(text.replace(",", ""))


def parse_item_page(page: str) -> dict:
    """Read one item page. Clubs with no counts are skipped."""
    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    sheet = SHEET.search(plain)
    product = PRODUCT.search(plain)
    states: dict[str, list] = {}
    for block in STATE_BLOCK.finditer(page):
        clubs = []
        for row in ROW.finditer(block["body"]):
            text = row["row"]
            name = NAME.search(text)
            club = CLUB.search(text)
            counts = {"hand": 0, "order": 0, "transit": 0}
            for kind, number in QUANTITY.findall(text):
                if kind in KINDS:
                    counts[KINDS[kind]] += count(number)
            if name and any(counts.values()):
                label = html.unescape(name.group(1)).strip()
                # A city-area listing names a city but not which club in it.
                clubs.append({"number": club.group(1) if club else "", "name": label if club else f"{label} area", **counts})
        if clubs:
            states[block["slug"]] = clubs
    return {
        "sheet_date": sheet.group(1) if sheet else "",
        "product": product.group(1) if product else "",
        "item": product.group(2) if product else "",
        "states": states,
    }


def fetch_items(session=None, pause: float = 1.5):
    """Find every Sam's item page and keep the clubs in the watched states. A refusal ends the pass."""
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    config = load_json(CONFIG_DIR / "costco.json")
    try:
        sitemap = session.get(SITEMAP_URL, timeout=25)
    except requests.RequestException as error:
        print(f"Sam's Club check stopped: {type(error).__name__}")
        return None
    if sitemap.status_code != OK:
        print(f"Sam's Club check stopped: the site returned {sitemap.status_code}")
        return None
    items = []
    for url in sorted(set(ITEM_PAGE.findall(sitemap.text)))[:MAX_ITEMS]:
        time.sleep(pause)
        try:
            response = session.get(url, timeout=25)
        except requests.RequestException as error:
            print(f"Sam's Club check stopped: {type(error).__name__}")
            break
        if response.status_code != OK:
            print(f"Sam's Club check stopped: the site returned {response.status_code}")
            break
        page = parse_item_page(response.text)
        if not page["sheet_date"]:
            print(f"Sam's Club page had no sheet date, so its layout may have changed: {url}")
            continue
        page["url"] = url
        page["states"] = {config["states"][slug]: clubs for slug, clubs in page["states"].items() if slug in config["states"]}
        page["focus"] = config.get("focus", "")
        page["area"] = config.get("area", "")
        items.append(page)
    print(f"Sam's Club check: {len(items)} items, {sum(len(c) for i in items for c in i['states'].values())} clubs nearby on their sheets.")
    return items


def refresh_if_due(now, state: dict, session=None, pause: float = 1.5):
    """Check every 30 minutes. Failed checks wait too, so a refusing site isn't hammered."""
    last = state.get("sams_checked_at")
    if last and now - parse_iso(last) < LOOK_EVERY:
        return None
    state["sams_checked_at"] = iso(now)
    return fetch_items(session, pause)
