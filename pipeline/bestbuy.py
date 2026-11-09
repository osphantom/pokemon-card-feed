"""Best Buy stock alerts, from Best Buy's official Developer API.

Best Buy's API terms shape how this works:
- Its data can be kept for 72 hours at most, so this public repo saves only one-way
  fingerprints of what's in stock, and alerts delete themselves after two days.
- Its data must be credited, with its logo at the bottom of each post.
- The key is for the owner alone, so everything posts to a channel only the owner can see.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from datetime import timedelta

import requests

from .common import CONFIG_DIR, DATA_DIR, USER_AGENT, fold, iso, load_json, parse_iso, save_json, utcnow
from .discord_api import Discord, DiscordError
from .discord_post import COLORS, WebhookClient, clip, discord_time, load_webhooks, money
from .market import compile_rules, match_rule

API = "https://api.bestbuy.com/v1"
KEY_ENV = "BESTBUY_API_KEY"
SEARCH = "search=pokemon&search=trading&search=card"
SHOW = "sku,name,salePrice,onlineAvailability,inStoreAvailability,orderable,url,addToCartUrl,thumbnailImage"
PAGE_SIZE = 100
MAX_PAGES = 3
# Best Buy allows 5 calls a second.
PAUSE = 0.3
OK = 200
NOT_FOUND = 404

CHANNEL = "best-buy"
STATE_PATH = DATA_DIR / "bestbuy_state.json"
KEEP_ALERTS_FOR = timedelta(hours=48)
# Stock that blinks in and out shouldn't ping over and over.
REALERT_AFTER = timedelta(hours=6)
MAX_ALERTS = 20
ALERTS_PER_MESSAGE = 5
# A price well above normal is usually a marketplace seller, which isn't worth a ping.
MAX_PING_MARKUP = 1.25
LOGO = "https://developer.bestbuy.com/images/bestbuy-logo.png"
CREDIT = {"text": "Uses Best Buy data", "icon_url": LOGO}
NOT_SEALED = re.compile(
    r"\b(sleeves?|binder(?! collection)|deck box|deck protectors?|play ?mat|portfolio|album|top ?loaders?|storage|plush|"
    r"video game|nintendo|japanese|korean|chinese|thai|indonesian|german|french|spanish|italian|portuguese)\b"
)
SAMPLE = {
    "sku": "0000000",
    "name": "TEST ALERT: nothing is really in stock",
    "price": 49.99,
    "normal": 49.99,
    "online": True,
    "preorder": False,
    "url": "https://www.bestbuy.com",
    "cart": "https://www.bestbuy.com",
    "image": None,
    "stores": [{"id": "0", "name": "Example Store", "low": True}],
}


class StockCheckFailed(Exception):
    """Best Buy refused or didn't answer, so this check tells us nothing."""


def api_get(session, path: str, params: dict, key: str):
    """Return the parsed answer, or None for something Best Buy doesn't have."""
    try:
        response = session.get(f"{API}{path}", params={**params, "apiKey": key}, timeout=25)
        if response.status_code == NOT_FOUND:
            return None
        if response.status_code != OK:
            raise StockCheckFailed(f"Best Buy returned {response.status_code}")
        return response.json()
    except (requests.RequestException, ValueError) as error:
        # Never print the error itself. It can include the URL, and the URL includes the key.
        raise StockCheckFailed(f"the request failed: {type(error).__name__}") from None


def list_products(session, key: str, pause: float) -> list[dict]:
    found: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = {"format": "json", "show": SHOW, "pageSize": PAGE_SIZE, "page": page}
        data = api_get(session, f"/products({SEARCH})", params, key)
        found.extend((data or {}).get("products", []))
        if not data or page >= data.get("totalPages", 1):
            break
        time.sleep(pause)
    return found


def classify(name: str, exclude: list, rules: list):
    """Return the product's price rule, a blank rule if sealed with no known price, or None to skip it."""
    folded = fold(name)
    if "pokemon" not in folded or not ("trading card" in folded or "tcg" in folded):
        return None
    if NOT_SEALED.search(folded) or any(pattern.search(folded) for pattern in exclude):
        return None
    return match_rule(folded, rules) or {"msrp": None}


def stores_with_stock(session, key: str, sku, config: dict) -> list[dict]:
    """Best Buy lists only the stores that have the item, within 250 miles of the ZIP."""
    data = api_get(session, f"/products/{sku}/stores.json", {"postalCode": config["postal_code"]}, key)
    return [
        {"id": str(store["storeID"]), "name": store.get("name") or store.get("city") or "Best Buy", "low": bool(store.get("lowStock"))}
        for store in (data or {}).get("stores", [])
        if store.get("state") in config["states"]
    ]


def fetch_stock(key: str, session=None, pause: float = PAUSE) -> list[dict]:
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    config = load_json(CONFIG_DIR / "bestbuy.json")
    exclude, rules = compile_rules(load_json(CONFIG_DIR / "products.json"))
    stock = []
    for item in list_products(session, key, pause):
        rule = classify(item.get("name", ""), exclude, rules)
        if rule is None:
            continue
        stores = []
        if item.get("inStoreAvailability"):
            time.sleep(pause)
            stores = stores_with_stock(session, key, item["sku"], config)
        stock.append({
            "sku": str(item["sku"]),
            "name": item["name"],
            "price": item.get("salePrice"),
            "normal": rule.get("msrp"),
            "online": item.get("onlineAvailability") is True,
            "preorder": fold(str(item.get("orderable") or "")) == "preorder",
            "url": item.get("url") or "https://www.bestbuy.com",
            "cart": item.get("addToCartUrl"),
            "image": item.get("thumbnailImage"),
            "stores": stores,
        })
    # GitHub's run logs are public, so only counts go here.
    online = sum(product["online"] for product in stock)
    in_stores = sum(bool(product["stores"]) for product in stock)
    print(f"Best Buy check: {len(stock)} sealed products, {online} online, {in_stores} in nearby stores.")
    return stock


def fingerprint(*parts: str) -> str:
    """A one-way code, so the public repo holds none of Best Buy's data."""
    return hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]


def facts_of(stock: list[dict]) -> set[str]:
    facts = set()
    for product in stock:
        if product["online"]:
            facts.add(fingerprint(product["sku"], "online"))
        facts |= {fingerprint(product["sku"], store["id"]) for store in product["stores"]}
    return facts


def find_news(stock: list[dict], known: set, alerted: dict) -> list[dict]:
    """Find what wasn't in stock last time and hasn't pinged recently."""
    is_news = lambda fact: fact not in known and fact not in alerted
    news = []
    for product in stock:
        online_fact = fingerprint(product["sku"], "online")
        back_online = product["online"] and is_news(online_fact)
        new_stores = [store for store in product["stores"] if is_news(fingerprint(product["sku"], store["id"]))]
        if back_online or new_stores:
            facts = [online_fact] if back_online else []
            facts += [fingerprint(product["sku"], store["id"]) for store in new_stores]
            news.append({"product": product, "online": back_online, "facts": facts})
    return news


def price_text(product: dict) -> str:
    if product["price"] is None:
        return "See the page"
    if product["normal"] and product["price"] != product["normal"]:
        return f"{money(product['price'])} (normal {money(product['normal'])})"
    return money(product["price"])


def worth_a_ping(product: dict) -> bool:
    return not product["normal"] or product["price"] is None or product["price"] <= product["normal"] * MAX_PING_MARKUP


def store_names(stores: list[dict]) -> str:
    return ", ".join(f"{store['name']}{' (low stock)' if store['low'] else ''}" for store in stores)


def online_text(product: dict) -> str:
    if not product["online"]:
        return "Not right now"
    return "Preorder" if product["preorder"] else "In stock"


def alert_embed(entry: dict, area: str) -> dict:
    product = entry["product"]
    if entry["online"]:
        headline = "Preorder open" if product["preorder"] else "Back online"
    else:
        headline = f"In stock in {area}"
    links = f"[Product page]({product['url']})"
    if product["cart"]:
        links = f"[Add to cart]({product['cart']}) · {links}"
    fields = [
        {"name": "Price", "value": price_text(product), "inline": True},
        {"name": "SKU", "value": product["sku"], "inline": True},
        {"name": "Online", "value": online_text(product), "inline": True},
    ]
    if product["stores"]:
        fields.append({"name": f"{area} stores", "value": clip(store_names(product["stores"]), 400)})
    fields.append({"name": "Buy", "value": links})
    embed = {
        "title": clip(f"🟢 {headline}: {product['name']}", 200),
        "url": product["url"],
        "color": COLORS["restocks"],
        "fields": fields,
        "footer": CREDIT,
    }
    if product["image"]:
        embed["thumbnail"] = {"url": product["image"]}
    return embed


def with_owner_ping(payload: dict, owner_id: str, note: str = "") -> dict:
    """Ping the owner by name. Mentioning everyone can't reach a channel hidden from everyone."""
    return {**payload, "content": f"<@{owner_id}>{note}", "allowed_mentions": {"users": [owner_id]}}


def post_news(client, state: dict, news: list[dict], now, area: str, owner_id: str) -> None:
    shown = news[:MAX_ALERTS]
    for start in range(0, len(shown), ALERTS_PER_MESSAGE):
        batch = shown[start : start + ALERTS_PER_MESSAGE]
        payload = {"embeds": [alert_embed(entry, area) for entry in batch]}
        if start == 0 and any(worth_a_ping(entry["product"]) for entry in shown):
            payload = with_owner_ping(payload, owner_id)
        state["alerts"].append({"id": client.upsert(CHANNEL, payload, None), "at": iso(now)})
        for entry in batch:
            state["alerted"].update({fact: iso(now) for fact in entry["facts"]})


def fit_lines(lines: list[str], limit: int = 1024) -> str:
    """Join whole lines, so a link is never cut in half."""
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > limit - 20:
            return "\n".join(kept + [f"…and {len(lines) - len(kept)} more"])
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept) or "Nothing sealed right now."


def board(stock: list[dict], area: str, now) -> dict:
    online = [f"**{money(p['price'])}** · [{p['name']}]({p['url']})" for p in stock if p["online"] and p["price"] is not None]
    nearby = [f"[{p['name']}]({p['url']}): {clip(store_names(p['stores']), 90)}" for p in stock if p["stores"]]
    return {
        "embeds": [{
            "title": "🏷️ Best Buy stock right now",
            "url": "https://www.bestbuy.com",
            "color": COLORS["restocks"],
            "description": f"Last checked {discord_time(iso(now), 'R')}. You'll get a ping here when something comes back.",
            "fields": [
                {"name": "Online", "value": fit_lines(online)},
                {"name": f"In {area} stores", "value": fit_lines(nearby)},
            ],
            "footer": CREDIT,
        }]
    }


def delete_expired(client, state: dict, now) -> None:
    keep = []
    for alert in state.get("alerts", []):
        if now - parse_iso(alert["at"]) >= KEEP_ALERTS_FOR:
            client.delete(CHANNEL, alert["id"])
        else:
            keep.append(alert)
    state["alerts"] = keep


def run(now, client, state: dict, stock: list[dict], area: str, owner_id: str) -> None:
    """Post what's new and refresh the list. The first check only records where things stand."""
    known = set(state["facts"]) if "facts" in state else None
    state.setdefault("alerts", [])
    state["alerted"] = {fact: at for fact, at in state.get("alerted", {}).items() if now - parse_iso(at) < REALERT_AFTER}
    if known is not None:
        post_news(client, state, find_news(stock, known, state["alerted"]), now, area, owner_id)
    # The list is rewritten on every check, so what it shows is never old.
    state["board_id"] = client.upsert(CHANNEL, board(stock, area, now), state.get("board_id"))
    state["facts"] = sorted(facts_of(stock))


def post_sample(client, owner_id: str, area: str) -> None:
    """Show what an alert looks like, and prove the bot can delete its own messages."""
    entry = {"product": SAMPLE, "online": True, "facts": []}
    note = " This is only a test of your Best Buy alerts. Nothing is really in stock."
    client.upsert(CHANNEL, with_owner_ping({"embeds": [alert_embed(entry, area)]}, owner_id, note), None)
    throwaway = client.upsert(CHANNEL, {"content": "Checking that this bot can tidy up after itself."}, None)
    client.delete(CHANNEL, throwaway)


def main(argv: list[str]) -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    key = (os.environ.get(KEY_ENV) or "").strip()
    sample = "--sample" in argv
    if not token or not guild_id:
        print("Discord isn't set up yet; skipping.")
        return 0
    if not sample and not key:
        print("No Best Buy key yet; skipping.")
        return 0

    api = Discord(token)
    area = load_json(CONFIG_DIR / "bestbuy.json")["area"]
    try:
        webhooks = load_webhooks(api, guild_id, load_json(CONFIG_DIR / "discord.json"))
        if CHANNEL not in webhooks:
            print("The Best Buy channel isn't ready. Run the Set up Discord workflow first.")
            return 0
        client = WebhookClient(api, webhooks)
        owner_id = api.guild(guild_id)["owner_id"]
        if sample:
            post_sample(client, owner_id, area)
            print("Test alert posted, and deleting a message worked.")
            return 0

        now = utcnow()
        state = load_json(STATE_PATH, {})
        try:
            delete_expired(client, state, now)
            try:
                stock = fetch_stock(key)
            except StockCheckFailed as error:
                print(f"Best Buy check stopped: {error}")
                return 0
            run(now, client, state, stock, area, owner_id)
        finally:
            save_json(STATE_PATH, state)
    except DiscordError as error:
        print(f"Discord posting stopped: {error}")
        return 1
    print("Best Buy alerts are up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
