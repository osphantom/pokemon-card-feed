"""Costco shipment counts from PokeRestock's public state pages.

PokeRestock's robots rules allow these pages and it publishes no terms against reading
them. The numbers come from dated allocation sheets, not live shelf stock. They go only
to the private Discord server with a link back, and aren't stored in this public repo.
"""
from __future__ import annotations

import html
import re
import time
from datetime import timedelta

import requests

from .common import CONFIG_DIR, USER_AGENT, iso, load_json, parse_iso

BASE_URL = "https://pokerestockapp.com/costco/"
REFRESH_AFTER = timedelta(hours=6)
ROW = re.compile(
    r'class="nm">(?P<name>[^<]+)</span>\s*<span class="ct">#(?P<number>\d+)</span>'
    r'.*?data-l="On hand">(?P<hand>[\d,]+)<'
    r'.*?data-l="On order">(?P<order>[\d,]+)<'
    r'.*?data-l="In transit">(?P<transit>[\d,]+)<',
    re.S,
)
NOT_FOUND = 404
OK = 200


def count(text: str) -> int:
    return int(text.replace(",", ""))


def parse_page(page: str) -> dict:
    """Read one state page. Warehouses that aren't on the sheet have no counts and are skipped."""
    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    sheet = re.search(r"Sheet date:\s*([A-Z][a-z]+ \d{1,2})", plain)
    product = re.search(r"totals\s+(.+?)\s+·\s+item\s+(\d+)", plain)
    warehouses = []
    for chunk in page.split("<tr")[1:]:
        match = ROW.search(chunk)
        if match:
            warehouses.append(
                {
                    "number": match["number"],
                    "name": html.unescape(match["name"]).strip(),
                    "hand": count(match["hand"]),
                    "order": count(match["order"]),
                    "transit": count(match["transit"]),
                }
            )
    return {
        "sheet_date": sheet.group(1) if sheet else "",
        "product": product.group(1) if product else "",
        "item": product.group(2) if product else "",
        "warehouses": warehouses,
    }


def status_of(warehouse: dict) -> str:
    if warehouse["hand"] > 0:
        return "hand"
    if warehouse["transit"] > 0:
        return "transit"
    if warehouse["order"] > 0:
        return "order"
    return "none"


def fetch_states(session=None, pause: float = 1.5):
    """Read each configured state once. A refusal ends the pass instead of retrying."""
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    config = load_json(CONFIG_DIR / "costco.json")
    states: dict[str, list] = {}
    details = {"sheet_date": "", "product": "", "item": ""}
    for slug, abbreviation in config["states"].items():
        try:
            response = session.get(f"{BASE_URL}{slug}/", timeout=25)
        except requests.RequestException as error:
            print(f"Costco check stopped: {type(error).__name__}")
            break
        if response.status_code == NOT_FOUND:
            continue
        if response.status_code != OK:
            print(f"Costco check stopped: the site returned {response.status_code}")
            break
        page = parse_page(response.text)
        if page["warehouses"]:
            states[abbreviation] = page["warehouses"]
            for key in details:
                details[key] = details[key] or page[key]
        time.sleep(pause)
    if not states:
        print("Costco check found no warehouses; the page layout may have changed.")
        return None
    print(f"Costco check: {sum(len(rows) for rows in states.values())} warehouses on the sheet in {len(states)} states.")
    return {**details, "states": states}


def refresh_if_due(now, state: dict, session=None, pause: float = 1.5):
    """Check a few times a day. Failed checks wait too, so a refusing site isn't hammered."""
    last = state.get("costco_checked_at")
    if last and now - parse_iso(last) < REFRESH_AFTER:
        return None
    state["costco_checked_at"] = iso(now)
    return fetch_states(session, pause)
