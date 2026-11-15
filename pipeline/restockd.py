"""Restockd.app drop-news: the pre-drop signals and release schedules its site posts.

Restockd publishes a public "Pokemon News" feed (Costco queue-incoming notices with a
store link, Target/Walmart release schedules, drawing results). This reads that feed a
couple of times a day and hands the new items to the Discord poster, which routes each to
its store channel. The site is national — it has no zip filter on the web, so these are
nationwide signals (online queues/releases are national timing anyway). Counts per
warehouse still come from pokerestockapp via costco_stock / sams_stock.

Only the public page is read, gently (one request, at most ~twice a day). Nothing is stored
in this public repo beyond a dedupe key.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

import requests

from .common import USER_AGENT, iso, parse_iso


def _parse_card_date(text: str) -> datetime:
    """Restockd stamps cards like 2026-11-13T16:23:56.544+00:00 — offsets and fractions, not the Z form."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

# The Pokémon news feed lives on this page alongside the drop history.
NEWS_URL = "https://restockd.app/pokemon-center-restocks"
# Twice a day: only fetch if it's been this long since the last look.
LOOK_EVERY = timedelta(hours=11)
# Don't post anything older than this (avoids dumping stale history on the first run).
FRESH_DAYS = 10
MAX_ITEMS = 12
OK = 200

# Each news card is a <time> with an ISO date, a bold headline paragraph, then muted body
# paragraphs. The headline's distinctive classes separate news cards from the drop-history
# list (whose <time> entries are followed by "N PRODUCTS", not a headline paragraph).
CARD = re.compile(
    r'<time[^>]*dateTime="(?P<iso>[^"]+)"[^>]*>(?P<date>[^<]*)</time>'
    r'<p class="[^"]*font-medium text-foreground text-sm[^"]*">(?P<head>.*?)</p>'
    r'(?P<rest>.*?)(?=<time[^>]*dateTime=|</article|<footer|</main|$)',
    re.S,
)
BODY_P = re.compile(r'<p class="[^"]*text-muted-foreground[^"]*">(?P<p>.*?)</p>', re.S)
LINK = re.compile(r'href="(https?://[^"]+)"')

# Which store a headline belongs to, so the poster can route it. First match wins.
STORES = [
    ("costco", re.compile(r"costco", re.I)),
    ("sams-club", re.compile(r"sam'?s club|sams club", re.I)),
    ("walmart", re.compile(r"walmart", re.I)),
    ("target", re.compile(r"target", re.I)),
    ("pokemon-center", re.compile(r"pok[eé]mon center|pokemoncenter", re.I)),
    ("best-buy", re.compile(r"best buy|bestbuy", re.I)),
]


def strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def store_of(headline: str, body: str) -> str:
    text = f"{headline} {body}"
    for store, pattern in STORES:
        if pattern.search(text):
            return store
    return "other-stores"


def parse_news(page: str) -> list[dict]:
    """Pull the news cards out of the page, newest first as the page orders them."""
    out = []
    for card in CARD.finditer(page):
        headline = strip_tags(card["head"])
        if not headline:
            continue
        rest = card["rest"]
        body = " ".join(strip_tags(block["p"]) for block in BODY_P.finditer(rest)).strip()
        links = []
        for block in BODY_P.finditer(rest):
            for url in LINK.findall(block["p"]):
                if url not in links:
                    links.append(url)
        out.append(
            {
                "iso": card["iso"],
                "date": strip_tags(card["date"]),
                "headline": headline,
                "body": body,
                "links": links,
                "store": store_of(headline, body),
            }
        )
    return out


def fetch_news(session=None) -> list[dict] | None:
    """Read the news page once. A refusal returns None instead of retrying."""
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        response = session.get(NEWS_URL, timeout=25)
    except requests.RequestException as error:
        print(f"Restockd check stopped: {type(error).__name__}")
        return None
    if response.status_code != OK:
        print(f"Restockd check stopped: the site returned {response.status_code}")
        return None
    news = parse_news(response.text)
    if not news:
        print("Restockd check found no news cards; the page layout may have changed.")
        return None
    print(f"Restockd check: {len(news)} news items on the page.")
    return news


def recent(news: list[dict], now) -> list[dict]:
    """Keep only items dated within the last FRESH_DAYS, newest first, capped."""
    cutoff = now - timedelta(days=FRESH_DAYS)
    kept = []
    for item in news:
        try:
            when = _parse_card_date(item["iso"])
        except (ValueError, KeyError):
            continue
        if when >= cutoff:
            kept.append(item)
    kept.sort(key=lambda entry: entry["iso"], reverse=True)
    return kept[:MAX_ITEMS]


def refresh_if_due(now, state: dict, session=None) -> list[dict] | None:
    """Read the news feed at most ~twice a day. Failed checks wait too, so the site isn't hammered."""
    last = state.get("restockd_checked_at")
    if last and now - parse_iso(last) < LOOK_EVERY:
        return None
    state["restockd_checked_at"] = iso(now)
    news = fetch_news(session)
    if news is None:
        return None
    return recent(news, now)
