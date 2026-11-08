"""Write RSS 2.0 files."""
from __future__ import annotations

from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape

from .common import SITE_URL, parse_iso


def rfc822(iso_text: str) -> str:
    return format_datetime(parse_iso(iso_text), usegmt=True)


def render_item(item: dict) -> str:
    parts = [
        "<item>",
        f"<title>{escape(item['title'])}</title>",
        f"<link>{escape(item['link'])}</link>",
        f'<guid isPermaLink="false">{escape(item["guid"])}</guid>',
        f"<pubDate>{rfc822(item['published'])}</pubDate>",
        f"<category>{escape(item['category'])}</category>",
    ]
    if item.get("description"):
        parts.append(f"<description>{escape(item['description'])}</description>")
    parts.append("</item>")
    return "".join(parts)


def render_feed(title: str, description: str, path: str, items: list[dict], now: datetime) -> str:
    body = "\n".join(render_item(item) for item in items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
        f"<title>{escape(title)}</title>\n"
        f"<link>{escape(SITE_URL)}</link>\n"
        f"<description>{escape(description)}</description>\n"
        "<language>en-us</language>\n"
        f"<lastBuildDate>{format_datetime(now, usegmt=True)}</lastBuildDate>\n"
        f'<atom:link href="{escape(SITE_URL + path)}" rel="self" type="application/rss+xml"/>\n'
        f"{body}\n</channel>\n</rss>\n"
    )
