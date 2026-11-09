"""Sealed product prices from TCGCSV (daily TCGplayer data) and, with keys, eBay asks."""
from __future__ import annotations

import base64
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

import requests

from .common import CONFIG_DIR, DATA_DIR, EASTERN, USER_AGENT, fold, iso, load_json, parse_iso, save_json
from .profit import estimate

TCGCSV = "https://tcgcsv.com/tcgplayer/3"
FIRST_GROUP_ID = 23821  # Prismatic Evolutions. Earlier sets are not at retail.
GROUP_NAME = re.compile(r"^(ME|SV)\d*:")
SKIP_GROUP = re.compile(r"promo|energies", re.I)
REFRESH_AFTER = timedelta(hours=11)

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_LOOKUPS_PER_REFRESH = 60

TITLE_REJECT = re.compile(
    r"\b(empty|opened|open box|no packs|japanese|korean|chinese|box only|codes?|lot of|bulk|proxy|custom|"
    r"damaged|dented|crushed|case)\b"
)
TITLE_STOPWORDS = {"pokemon", "the", "of", "and", "exclusive", "international", "version"}
ETB_WORDS = {"elite", "trainer", "box"}


def modern_groups(groups: list[dict]) -> list[dict]:
    return [
        group
        for group in groups
        if group["groupId"] >= FIRST_GROUP_ID
        and GROUP_NAME.match(group["name"])
        and not SKIP_GROUP.search(group["name"])
    ]


def compile_rules(config: dict) -> tuple[list, list]:
    exclude = [re.compile(pattern) for pattern in config["exclude"]]
    rules = [{**rule, "pattern": re.compile(rule["match"])} for rule in config["rules"]]
    return exclude, rules


def match_rule(folded_name: str, rules: list):
    return next((rule for rule in rules if rule["pattern"].search(folded_name)), None)


def release_date(product: dict):
    released = (product.get("presaleInfo") or {}).get("releasedOn")
    return released[:10] if released else None


def search_phrase(name: str) -> str:
    return re.sub(r"[\[\]()]", " ", name) + " sealed"


def ebay_sold_url(name: str) -> str:
    return f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(search_phrase(name))}&LH_Sold=1&LH_Complete=1"


def set_label(group_name: str) -> str:
    return re.sub(r"^(ME|SV)\d*:\s*", "", group_name)


def product_rows(group, products, prices, config, assumptions, today: date):
    """Turn one set's TCGplayer rows into flip-list rows plus any presale notices."""
    exclude, rules = compile_rules(config)
    price_by_id: dict[int, dict] = {}
    for row in prices:
        price_by_id.setdefault(row["productId"], row)

    rows, presale = [], []
    for product in products:
        if any(entry.get("name") == "Number" for entry in product.get("extendedData") or []):
            continue
        name = product["name"]
        folded = fold(name)
        if any(pattern.search(folded) for pattern in exclude):
            continue
        rule = match_rule(folded, rules)
        if rule is None:
            continue

        release = release_date(product)
        is_presale = bool((product.get("presaleInfo") or {}).get("isPresale"))
        if is_presale and release:
            presale.append({"id": product["productId"], "name": name, "set": set_label(group["name"]), "release": release})

        market = price_by_id.get(product["productId"], {}).get("marketPrice")
        if not market or rule["msrp"] is None:
            continue
        result = estimate(market, rule["msrp"], rule["ship"], assumptions)
        days_out = (today - date.fromisoformat(release)).days if release else 9999
        rows.append(
            {
                "id": product["productId"],
                "name": name,
                "set": set_label(group["name"]),
                "type": rule["type"],
                "group": rule["group"],
                "msrp": rule["msrp"],
                "market": round(market, 2),
                "ship": rule["ship"],
                "release": release,
                "presale": is_presale,
                "current": days_out <= config["current_days"],
                "url": product.get("url") or "",
                "sold_url": ebay_sold_url(name),
                "ebay_ask": None,
                "ebay_count": 0,
                "profit": result["profit"],
                "roi": result["roi"],
            }
        )
    return rows, presale


def title_matches(product_name: str, title: str) -> bool:
    folded_title = fold(title)
    if TITLE_REJECT.search(folded_title):
        return False
    product = fold(re.sub(r"\[.*?\]|\(.*?\)", " ", product_name))
    if "pokemon center" not in product and "pokemon center" in folded_title:
        return False
    title_words = set(re.findall(r"[a-z0-9]+", folded_title))
    if "etb" in title_words:
        title_words |= ETB_WORDS
    needed = {word for word in re.findall(r"[a-z0-9]+", product) if word not in TITLE_STOPWORDS}
    return needed <= title_words


def ask_from_listings(product_name: str, market: float, listings: list[dict]):
    """Median of the five cheapest believable listings, including shipping."""
    totals = []
    for listing in listings:
        if not title_matches(product_name, listing.get("title", "")):
            continue
        try:
            price = float(listing["price"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        shipping = 0.0
        options = listing.get("shippingOptions") or []
        if options:
            try:
                shipping = float(options[0]["shippingCost"]["value"])
            except (KeyError, TypeError, ValueError):
                shipping = 0.0
        total = price + shipping
        if 0.6 * market <= total <= 2 * market:
            totals.append(total)
    totals.sort()
    if not totals:
        return None, 0
    return round(statistics.median(totals[:5]), 2), len(totals)


class EbayClient:
    def __init__(self, client_id: str, client_secret: str, session):
        self.session = session
        self.basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self.token = None

    def authenticate(self) -> bool:
        response = self.session.post(
            EBAY_TOKEN_URL,
            headers={"Authorization": f"Basic {self.basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=30,
        )
        if response.status_code != 200:
            print(f"eBay sign-in returned {response.status_code}; skipping eBay asks.")
            return False
        self.token = response.json()["access_token"]
        return True

    def search(self, query: str) -> list[dict]:
        response = self.session.get(
            EBAY_SEARCH_URL,
            headers={"Authorization": f"Bearer {self.token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
            params={
                "q": query,
                "limit": "50",
                "sort": "price",
                "filter": "buyingOptions:{FIXED_PRICE},conditions:{NEW},itemLocationCountry:US",
            },
            timeout=30,
        )
        if response.status_code != 200:
            return []
        return response.json().get("itemSummaries", []) or []


def add_ebay_asks(rows: list[dict], session) -> bool:
    # A pasted key often carries a stray space or line break.
    client_id = os.environ.get("EBAY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return False
    client = EbayClient(client_id, client_secret, session)
    try:
        if not client.authenticate():
            return False
        lookups = [row for row in rows if row["current"]][:EBAY_LOOKUPS_PER_REFRESH]
        for row in lookups:
            listings = client.search(search_phrase(row["name"]))
            row["ebay_ask"], row["ebay_count"] = ask_from_listings(row["name"], row["market"], listings)
            time.sleep(0.25)
    except requests.RequestException as error:
        print(f"eBay lookup stopped early: {type(error).__name__}")
    return True


def build_market(now: datetime, session=None) -> dict:
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    config = load_json(CONFIG_DIR / "products.json")
    assumptions = load_json(CONFIG_DIR / "assumptions.json")
    today = now.astimezone(EASTERN).date()

    groups = session.get(f"{TCGCSV}/groups", timeout=30).json()["results"]
    rows, presale = [], []
    for group in sorted(modern_groups(groups), key=lambda g: g["groupId"], reverse=True):
        products = session.get(f"{TCGCSV}/{group['groupId']}/products", timeout=30).json()["results"]
        prices = session.get(f"{TCGCSV}/{group['groupId']}/prices", timeout=30).json()["results"]
        new_rows, new_presale = product_rows(group, products, prices, config, assumptions, today)
        rows.extend(new_rows)
        presale.extend(new_presale)
        time.sleep(0.25)

    rows.sort(key=lambda row: (row["current"], row["profit"]), reverse=True)
    used_ebay = add_ebay_asks(rows, session)
    presale.sort(key=lambda item: (item["release"], item["name"]))
    return {"generated_at": iso(now), "ebay_used": used_ebay, "products": rows, "presale": presale}


def refresh_if_due(now: datetime, session=None, force: bool = False) -> dict:
    """Refresh about twice a day. If the source is down, keep the last good data."""
    path = DATA_DIR / "market.json"
    current = load_json(path)
    if current and not force and now - parse_iso(current["generated_at"]) < REFRESH_AFTER:
        return current
    try:
        fresh = build_market(now, session)
    except (requests.RequestException, ValueError, KeyError) as error:
        print(f"Market refresh failed, keeping last data: {type(error).__name__}")
        return current or {"generated_at": iso(now), "ebay_used": False, "products": [], "presale": []}
    save_json(path, fresh)
    return fresh
