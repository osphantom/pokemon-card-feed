"""Build the Discord server: categories, channels, webhooks, and starter guides.

Safe to run more than once. It only adds what's missing and never deletes anything.
"""
from __future__ import annotations

import os
import sys

from .common import CONFIG_DIR, load_json
from .discord_api import Discord, DiscordError, split_message

TYPE_TEXT = 0
TYPE_CATEGORY = 4


def ensure_category(api, guild_id, existing, name):
    for channel in existing:
        if channel["type"] == TYPE_CATEGORY and channel["name"].lower() == name.lower():
            return channel
    created = api.create_channel(guild_id, {"name": name, "type": TYPE_CATEGORY})
    existing.append(created)
    print(f"Created category {name}")
    return created


def ensure_channel(api, guild_id, existing, spec, parent_id):
    for channel in existing:
        if channel["type"] == TYPE_TEXT and channel["name"] == spec["name"]:
            if channel.get("topic") != spec.get("topic") or channel.get("parent_id") != parent_id:
                api.update_channel(channel["id"], {"topic": spec.get("topic", ""), "parent_id": parent_id})
            return channel
    body = {"name": spec["name"], "type": TYPE_TEXT, "topic": spec.get("topic", ""), "parent_id": parent_id}
    created = api.create_channel(guild_id, body)
    existing.append(created)
    print(f"Created channel #{spec['name']}")
    return created


def ensure_webhook(api, channel_id, name):
    for webhook in api.channel_webhooks(channel_id):
        if webhook.get("name") == name and webhook.get("token"):
            return webhook
    return api.create_webhook(channel_id, name)


def post_static(api, channel_id, webhook, filename):
    if api.has_messages(channel_id):
        return
    text = (CONFIG_DIR / "discord" / filename).read_text(encoding="utf-8")
    for chunk in split_message(text):
        api.post(webhook, {"content": chunk})
    print(f"Posted guide {filename}")


def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id:
        print("Add the DISCORD_BOT_TOKEN and DISCORD_GUILD_ID secrets first. See the README.")
        return 1

    config = load_json(CONFIG_DIR / "discord.json")
    api = Discord(token)
    try:
        existing = api.channels(guild_id)
        for category in config["categories"]:
            parent = ensure_category(api, guild_id, existing, category["name"])
            for spec in category["channels"]:
                channel = ensure_channel(api, guild_id, existing, spec, parent["id"])
                if spec.get("post") or spec.get("static"):
                    webhook = ensure_webhook(api, channel["id"], config["webhook_name"])
                    if spec.get("static"):
                        post_static(api, channel["id"], webhook, spec["static"])
    except DiscordError as error:
        print(f"Setup stopped: {error}")
        print("A 403 usually means the bot needs to be invited again with the link in the README.")
        return 1
    print("Discord server is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
