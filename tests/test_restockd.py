import unittest
from datetime import datetime, timezone

from pipeline import discord_post, restockd

NOW = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)

# Two news cards in Restockd's real shape, plus a drop-history <time> that must be ignored.
PAGE = """
<time dateTime="2026-11-13T16:23:56+00:00" title="Nov 13, 2026">Nov 13, 2026</time>\
<p class="whitespace-pre-line font-medium text-foreground text-sm leading-[1.5]"><span>Costco queue page is up!</span></p>\
<p class="whitespace-pre-line mt-2.5 text-[0.8125rem] text-muted-foreground leading-[1.5]"><span>30th 2PK Bundle $125.99 </span>\
<a class="x" href="https://www.costco.com/.product.2007497.html" target="_blank">costco.com</a></p>
<time dateTime="2026-11-12T20:00:00+00:00" title="Nov 12, 2026">Nov 12, 2026</time>\
<p class="whitespace-pre-line font-medium text-foreground text-sm"><span>Target release tonight ~3 AM ET</span></p>\
<p class="whitespace-pre-line text-muted-foreground"><span>Binder Collection and Unova Heavy Hitters</span></p>
<time dateTime="2024-01-01T00:00:00+00:00" title="Jan 1, 2024">Jan 1, 2024</time><span>6 PRODUCTS</span>
"""


class FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        return message_id or "id"


class ParseTests(unittest.TestCase):
    def test_parses_cards_and_routes_by_store(self):
        news = restockd.parse_news(PAGE)
        # The drop-history <time> (no headline paragraph) is not a news card.
        self.assertEqual(len(news), 2)
        costco, target = news
        self.assertEqual(costco["store"], "costco")
        self.assertEqual(costco["headline"], "Costco queue page is up!")
        self.assertIn("https://www.costco.com/.product.2007497.html", costco["links"])
        self.assertEqual(target["store"], "target")
        self.assertIn("Binder Collection", target["body"])

    def test_recent_drops_stale_items(self):
        kept = restockd.recent(restockd.parse_news(PAGE), NOW)
        self.assertEqual(len(kept), 2)  # the 2024 card is history, not a news card anyway
        self.assertTrue(all(item["iso"].startswith("2026") for item in kept))
        # newest first
        self.assertEqual(kept[0]["iso"][:10], "2026-11-13")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.news = restockd.recent(restockd.parse_news(PAGE), NOW)

    def test_first_run_records_but_posts_nothing(self):
        client, state, keys = FakeClient(), {}, set()
        discord_post.apply_restockd(client, state, self.news, keys)
        self.assertEqual(client.sent, [])
        self.assertEqual(len(keys), 2)  # both recorded so they never post as "new" later

    def test_posts_new_items_to_store_channels(self):
        client, state, keys = FakeClient(), {"initialized": True}, set()
        discord_post.apply_restockd(client, state, self.news, keys)
        channels = [channel for channel, _ in client.sent]
        self.assertIn("costco", channels)
        self.assertIn("target", channels)
        # A live Costco queue also pings the drop channel.
        self.assertIn("drop-pings", channels)

    def test_dedupes_on_second_pass(self):
        client, state, keys = FakeClient(), {"initialized": True}, set()
        discord_post.apply_restockd(client, state, self.news, keys)
        before = len(client.sent)
        discord_post.apply_restockd(client, state, self.news, keys)
        self.assertEqual(len(client.sent), before)  # nothing re-posted

    def test_costco_ping_has_next_morning_note(self):
        client, state, keys = FakeClient(), {"initialized": True}, set()
        discord_post.apply_restockd(client, state, self.news, keys)
        pings = [p for c, p in client.sent if c == "drop-pings"]
        self.assertTrue(pings)
        self.assertIn("next morning", pings[0]["embeds"][0]["description"])


if __name__ == "__main__":
    unittest.main()
