"""Decide whether a feed entry belongs in the feed, and which channel it goes to."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .common import CONFIG_DIR, Terms, fold, load_json

CATEGORY_ORDER = ("preorders", "restocks", "deals", "releases", "market")
BUYING_CATEGORIES = {"preorders", "restocks", "deals"}
PRICE_IN_TITLE = re.compile(r"\$\s?\d|\d+\s?% off")
MIN_TITLE_WORDS = 3


class Rules:
    def __init__(self, config: dict):
        self.reject = Terms(config["always_reject"])
        self.reject_prefixes = tuple(config["reject_prefixes"])
        self.not_deal = Terms(config["not_deal_terms"])
        self.blocked_domains = tuple(config["blocked_domains"])
        self.pokemon = Terms(config["require_pokemon"])
        self.card_terms = Terms(config["card_terms"])
        self.regional = Terms(config["regional_reject"])
        self.japanese = Terms(config["japanese_terms"])
        self.categories = {name: Terms(words) for name, words in config["categories"].items()}
        self.hot = Terms(config["hot_terms"])
        self.not_hot = Terms(config["not_hot_terms"])
        self.stores = [(label, Terms(words)) for label, words in config["stores"].items()]


def load_rules() -> Rules:
    return Rules(load_json(CONFIG_DIR / "filters.json"))


def categorize(text: str, source: dict, rules: Rules) -> str:
    for name in CATEGORY_ORDER[:2]:
        if rules.categories[name].search(text):
            return name
    if rules.categories["deals"].search(text):
        return "deals"
    if PRICE_IN_TITLE.search(text) and not rules.not_deal.search(text):
        return "deals"
    for name in CATEGORY_ORDER[3:]:
        if rules.categories[name].search(text):
            return name
    return source.get("default", "news")


def blocked_host(link: str, domains: tuple) -> bool:
    host = (urlsplit(link).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def classify(title: str, link: str, source: dict, rules: Rules):
    """Return {category, stores, hot} for a keeper, or None to drop the entry."""
    text = fold(title)
    if rules.reject.search(text) or blocked_host(link, rules.blocked_domains):
        return None
    # One- and two-word titles are forum usernames and profile pages.
    if len(text.split()) < MIN_TITLE_WORDS or text.startswith(rules.reject_prefixes):
        return None
    if not source.get("cards_only"):
        if not rules.pokemon.search(text) or not rules.card_terms.search(text):
            return None
    if source.get("only_terms") and not any(fold(term) in text for term in source["only_terms"]):
        return None

    category = categorize(text, source, rules)
    if category in BUYING_CATEGORIES and (rules.japanese.search(text) or rules.regional.search(text)):
        return None

    stores = [label for label, terms in rules.stores if terms.search(text)]
    hot = (
        category in ("preorders", "restocks")
        and rules.hot.search(text)
        and not rules.not_hot.search(text)
    )
    return {"category": category, "stores": stores, "hot": hot}
