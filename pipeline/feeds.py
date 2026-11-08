"""Fetch the public feeds and merge them into one deduplicated list."""
from __future__ import annotations

import calendar
import hashlib
import html
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .classify import classify
from .common import USER_AGENT, fold, iso, parse_iso

MAX_ENTRY_AGE = timedelta(days=21)
KEEP_FOR = timedelta(days=30)
MAX_ITEMS = 1000
PAGE_SUFFIX = re.compile(r"\s*\|\s*Page \d+$", re.I)


def clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def entry_time(entry, now: datetime) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return now
    moment = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return min(moment, now)


def normalize_entry(entry, source: dict, now: datetime):
    title = clean_text(entry.get("title", ""))
    link = (entry.get("link") or "").strip()
    if not title or not link.startswith(("http://", "https://")):
        return None

    publisher = ""
    if source.get("google_news"):
        publisher = clean_text((entry.get("source") or {}).get("title", ""))
        suffix = f" - {publisher}"
        if publisher and title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    # Forum threads show up once per page ("Some headline | Page 2").
    title = PAGE_SUFFIX.sub("", title).strip()

    published = entry_time(entry, now)
    if now - published > MAX_ENTRY_AGE:
        return None
    return {
        "title": title,
        "link": link,
        "source": source["name"],
        "source_id": source["id"],
        "publisher": publisher,
        "published": iso(published),
    }


def story_id(title: str, published: str) -> str:
    """Same headline in the same week counts as one story, across sources."""
    words = re.sub(r"[^a-z0-9 ]", "", fold(title))[:90]
    year, week, _ = parse_iso(published).isocalendar()
    return hashlib.sha1(f"{words}|{year}-{week}".encode()).hexdigest()[:14]


def fetch_sources(sources, now, session=None, pause: float = 1.0):
    """Fetch each source once. A refusal or failure is recorded, never retried."""
    session = session or requests.Session()
    session.headers.update(
        {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    )
    entries, health = [], []
    for source in sources:
        record = {"id": source["id"], "name": source["name"], "ok": False, "status": "", "count": 0}
        try:
            response = session.get(source["url"], timeout=25)
            record["status"] = response.status_code
            if response.status_code == 200:
                parsed = feedparser.parse(response.content)
                normalized = [normalize_entry(e, source, now) for e in parsed.entries[:60]]
                kept = [item for item in normalized if item]
                entries.extend(kept)
                record.update(ok=True, count=len(kept))
        except requests.RequestException as error:
            record["status"] = type(error).__name__
        health.append(record)
        time.sleep(pause)
    return entries, health


def merge_items(existing, fresh, sources, rules, now):
    """Combine stored and new entries, reapplying the current filters to everything."""
    by_source = {source["id"]: source for source in sources}
    stories: dict[str, dict] = {}

    for item in list(existing) + list(fresh):
        source = by_source.get(item.get("source_id"))
        if source is None:
            continue
        decision = classify(item["title"], item["link"], source, rules)
        if decision is None:
            continue
        identifier = item.get("id") or story_id(item["title"], item["published"])
        current = stories.get(identifier)
        if current is None:
            stories[identifier] = {
                **item,
                **decision,
                "id": identifier,
                "first_seen": item.get("first_seen") or iso(now),
            }
        elif item["published"] < current["published"]:
            current["published"] = item["published"]

    cutoff = now - KEEP_FOR
    kept = [story for story in stories.values() if parse_iso(story["published"]) >= cutoff]
    kept.sort(key=lambda story: (story["published"], story["id"]), reverse=True)
    return kept[:MAX_ITEMS]
