"""Build the Discord server: categories, channels, webhooks, and starter guides.

Safe to run more than once. It only adds what's missing and never deletes anything.
"""
from __future__ import annotations

import os
import re
import sys

from .common import CONFIG_DIR, load_json
from .discord_api import Discord, DiscordError, normalized, split_message

TYPE_TEXT = 0
TYPE_CATEGORY = 4
TYPE_ROLE_OVERWRITE = 0
SEND_MESSAGES = 2048


def find_channel(existing: list[dict], kind: int, name: str):
    wanted = normalized(name)
    return next((c for c in existing if c["type"] == kind and normalized(c["name"]) == wanted), None)


def ensure_category(api, guild_id, existing, name):
    found = find_channel(existing, TYPE_CATEGORY, name)
    if found:
        return found
    created = api.create_channel(guild_id, {"name": name, "type": TYPE_CATEGORY})
    existing.append(created)
    print(f"Created category {name}")
    return created


def ensure_channel(api, guild_id, existing, spec, parent_id):
    found = find_channel(existing, TYPE_TEXT, spec["name"])
    if found:
        if found.get("topic") != spec.get("topic") or found.get("parent_id") != parent_id:
            api.update_channel(found["id"], {"topic": spec.get("topic", ""), "parent_id": parent_id})
        return found
    body = {
        "name": spec["name"],
        "type": TYPE_TEXT,
        "topic": spec.get("topic", ""),
        "parent_id": parent_id,
        # Only the feed posts here, which keeps every channel easy to read.
        "permission_overwrites": [{"id": guild_id, "type": TYPE_ROLE_OVERWRITE, "deny": str(SEND_MESSAGES), "allow": "0"}],
    }
    created = api.create_channel(guild_id, body)
    existing.append(created)
    print(f"Created channel {spec['name']}")
    return created


def ensure_webhook(api, channel_id, name):
    for webhook in api.channel_webhooks(channel_id):
        if webhook.get("name") == name and webhook.get("token"):
            return webhook
    return api.create_webhook(channel_id, name)


def fill_channel_links(text: str, channel_ids: dict) -> str:
    """Turn {{key}} into a clickable channel link."""
    return re.sub(r"\{\{([a-z-]+)\}\}", lambda match: f"<#{channel_ids[match.group(1)]}>", text)


def post_static(api, channel_id, webhook, filename, channel_ids):
    if api.has_messages(channel_id):
        return False
    text = fill_channel_links((CONFIG_DIR / "discord" / filename).read_text(encoding="utf-8"), channel_ids)
    for chunk in split_message(text):
        api.post(webhook, {"content": chunk})
    print(f"Posted guide {filename}")
    return True


def order_categories(api, guild_id, existing, config) -> None:
    """Put the new categories first and leave anything else after them."""
    ours = [find_channel(existing, TYPE_CATEGORY, category["name"])["id"] for category in config["categories"]]
    others = [c["id"] for c in existing if c["type"] == TYPE_CATEGORY and c["id"] not in ours]
    api.set_positions(guild_id, [{"id": channel_id, "position": index} for index, channel_id in enumerate(ours + others)])


def build_server(api, guild_id, config) -> dict:
    existing = api.channels(guild_id)
    channel_ids: dict[str, str] = {}
    specs = []
    for category in config["categories"]:
        parent = ensure_category(api, guild_id, existing, category["name"])
        for spec in category["channels"]:
            channel = ensure_channel(api, guild_id, existing, spec, parent["id"])
            channel_ids[spec["key"]] = channel["id"]
            specs.append(spec)

    try:
        order_categories(api, guild_id, existing, config)
    except DiscordError as error:
        print(f"Couldn't reorder categories, continuing: {error}")

    guides = 0
    for spec in specs:
        if spec.get("post") or spec.get("static"):
            webhook = ensure_webhook(api, channel_ids[spec["key"]], config["webhook_name"])
            if spec.get("static") and post_static(api, channel_ids[spec["key"]], webhook, spec["static"], channel_ids):
                guides += 1
    return {"channels": len(specs), "guides_posted": guides}


def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        print("Add the DISCORD_BOT_TOKEN and DISCORD_GUILD_ID secrets first. See the README.")
        return 1

    try:
        result = build_server(Discord(token), guild_id, load_json(CONFIG_DIR / "discord.json"))
    except DiscordError as error:
        print(f"Setup stopped: {error}")
        print("A 403 usually means the bot needs to be invited again with the link in the README.")
        return 1
    print(f"Discord server is ready: {result['channels']} channels, {result['guides_posted']} guides posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
