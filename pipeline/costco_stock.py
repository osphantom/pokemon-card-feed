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
# Every 30 minutes one small page says whether a new sheet is out. The state pages are only
# read when it is, and at least every 6 hours in case that page stops saying.
LOOK_EVERY = timedelta(minutes=30)
FULL_CHECK_EVERY = timedelta(hours=6)
INDEX_MODIFIED = re.compile(r'"dateModified":"([^"]+)"')
# The 30th Celebration Ultra-Premium Collection page says "coming soon" until store counts are posted.
UPC_URL = f"{BASE_URL}30th-celebration-upc/"
COMING_SOON = re.compile(r"In-store counts\s+Coming soon|Restock info is coming soon", re.I)
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
        if slug != next(iter(config["states"])):
            time.sleep(pause)
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
        for key in details:
            details[key] = details[key] or page[key]
        if page["warehouses"]:
            states[abbreviation] = page["warehouses"]
    if not details["sheet_date"]:
        print("Costco check found no sheet date; the site refused or its page layout changed.")
        return None
    print(f"Costco check: {sum(len(rows) for rows in states.values())} warehouses on the sheet in {len(states)} states.")
    return {
        **details,
        "states": states,
        "watched": sorted(config["states"].values()),
        "focus": config.get("focus", ""),
        "area": config.get("area", ""),
    }


def upc_counts_posted(session) -> bool | None:
    """Whether the UPC page lists store counts yet, or None if it couldn't be read."""
    try:
        response = session.get(UPC_URL, timeout=25)
    except requests.RequestException:
        return None
    if response.status_code != OK:
        return None
    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", response.text)))
    return not COMING_SOON.search(plain)


def refresh_upc_if_due(now, state: dict, session=None):
    """Look at the UPC page every 30 minutes until its store counts are up."""
    if state.get("upc_counts_seen"):
        return None
    last = state.get("upc_checked_at")
    if last and now - parse_iso(last) < LOOK_EVERY:
        return None
    state["upc_checked_at"] = iso(now)
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    return upc_counts_posted(session)


def sheet_stamp(session) -> str | None:
    """When the Costco index page last changed, "" if it doesn't say, or None if the site refused."""
    try:
        response = session.get(BASE_URL, timeout=25)
    except requests.RequestException:
        return None
    if response.status_code != OK:
        return None
    found = INDEX_MODIFIED.search(response.text)
    return found.group(1) if found else ""


def refresh_if_due(now, state: dict, session=None, pause: float = 1.5):
    """Look for a new sheet every 30 minutes and read the state pages when one shows up.
    Failed checks wait too, so a refusing site isn't hammered."""
    last = state.get("costco_checked_at")
    # Editing the list of states takes effect on the next run.
    watched = sorted(load_json(CONFIG_DIR / "costco.json")["states"])
    same_states = state.get("costco_watched") == watched
    if last and now - parse_iso(last) < LOOK_EVERY and same_states:
        return None
    state["costco_checked_at"] = iso(now)
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    stamp = sheet_stamp(session)
    if stamp is None:
        print("Costco check stopped: the site didn't answer.")
        return None
    read_at = state.get("costco_read_at")
    recently_read = bool(read_at) and now - parse_iso(read_at) < FULL_CHECK_EVERY
    if same_states and stamp and stamp == state.get("costco_stamp") and recently_read:
        return None
    time.sleep(pause)
    data = fetch_states(session, pause)
    if data is not None:
        state.update(costco_watched=watched, costco_stamp=stamp, costco_read_at=iso(now))
    return data
