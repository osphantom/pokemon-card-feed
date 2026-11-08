"""Paths, time helpers, and small utilities shared by the pipeline."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
SITE_DIR = ROOT / "site"

SITE_URL = "https://osphantom.github.io/pokemon-card-feed/"
# An honest client name. This project never presents itself as a browser and
# never retries a source that refuses it.
USER_AGENT = "pokemon-card-feed/1.0 (+https://github.com/osphantom/pokemon-card-feed)"
EASTERN = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def fold(text: str) -> str:
    """Lowercase, strip accents, and tidy spacing so 'Pokémon' matches 'pokemon'."""
    decomposed = unicodedata.normalize("NFKD", text.replace("’", "'"))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip().lower()


class Terms:
    """A set of words or phrases matched on whole-word boundaries in folded text."""

    def __init__(self, terms):
        options = sorted({fold(term) for term in terms}, key=len, reverse=True)
        joined = "|".join(re.escape(option) for option in options)
        self.pattern = re.compile(rf"(?<![a-z0-9])(?:{joined})(?![a-z0-9])") if options else None

    def search(self, folded_text: str) -> bool:
        return bool(self.pattern and self.pattern.search(folded_text))


def format_eastern(moment: datetime, with_day: bool = True) -> str:
    """Example: 'Wed 5:00 PM ET'. Works on Windows and Linux."""
    local = moment.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    clock = f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} ET"
    return f"{local:%a} {clock}" if with_day else clock
