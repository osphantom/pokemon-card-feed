"""A small Discord REST client. Error messages never include tokens or webhook URLs."""
from __future__ import annotations

import time

import requests

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/osphantom/pokemon-card-feed, 1.0)"
MAX_ATTEMPTS = 4


class DiscordError(Exception):
    pass


class Discord:
    def __init__(self, token: str, session=None):
        self.session = session or requests.Session()
        self.bot_headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}

    def _send(self, method: str, url: str, label: str, **kwargs):
        for _ in range(MAX_ATTEMPTS):
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as error:
                raise DiscordError(f"{label} failed: {type(error).__name__}") from None
            if response.status_code == 429:
                try:
                    wait = float(response.json().get("retry_after", 2))
                except ValueError:
                    wait = 2.0
                time.sleep(min(wait, 30) + 0.25)
                continue
            if response.status_code >= 500:
                time.sleep(2)
                continue
            if response.status_code >= 400:
                raise DiscordError(f"{label} returned {response.status_code}")
            return response.json() if response.content else None
        raise DiscordError(f"{label} kept failing after {MAX_ATTEMPTS} attempts")

    def api(self, method: str, path: str, label: str, **kwargs):
        return self._send(method, f"{API}{path}", label, headers=self.bot_headers, **kwargs)

    def channels(self, guild_id: str) -> list[dict]:
        return self.api("GET", f"/guilds/{guild_id}/channels", "Reading channels")

    def create_channel(self, guild_id: str, body: dict) -> dict:
        return self.api("POST", f"/guilds/{guild_id}/channels", f"Creating channel {body['name']}", json=body)

    def update_channel(self, channel_id: str, body: dict) -> dict:
        return self.api("PATCH", f"/channels/{channel_id}", "Updating a channel", json=body)

    def channel_webhooks(self, channel_id: str) -> list[dict]:
        return self.api("GET", f"/channels/{channel_id}/webhooks", "Reading channel webhooks")

    def guild_webhooks(self, guild_id: str) -> list[dict]:
        return self.api("GET", f"/guilds/{guild_id}/webhooks", "Reading server webhooks")

    def create_webhook(self, channel_id: str, name: str) -> dict:
        return self.api("POST", f"/channels/{channel_id}/webhooks", "Creating a webhook", json={"name": name})

    def has_messages(self, channel_id: str) -> bool:
        return bool(self.api("GET", f"/channels/{channel_id}/messages?limit=1", "Reading a channel"))

    def post(self, webhook: dict, payload: dict) -> None:
        """Post through a webhook. Nothing in a post can ping anyone."""
        url = f"{API}/webhooks/{webhook['id']}/{webhook['token']}?wait=true"
        body = {"username": webhook.get("name", "Card Feed"), "allowed_mentions": {"parse": []}, **payload}
        self._send("POST", url, "Posting a message", json=body, headers={"User-Agent": USER_AGENT})
        time.sleep(0.6)


def split_message(text: str, limit: int = 1900) -> list[str]:
    """Split on blank lines so each Discord message stays under the length limit."""
    chunks, current = [], ""
    for block in text.strip().split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks
