import unittest
from datetime import datetime, timedelta, timezone

from pipeline import common, discord_api, discord_post, discord_setup
from pipeline.common import iso

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)
CONFIG = common.load_json(common.CONFIG_DIR / "discord.json")
discord_api.time.sleep = lambda seconds: None


class FakeClient:
    def __init__(self, fail=False):
        self.sent = []
        self.boards = {}
        self.upserts = 0
        self.fail = fail

    def send(self, channel, payload):
        if self.fail:
            raise discord_api.DiscordError("no")
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.upserts += 1
        self.boards[channel] = payload
        return message_id or f"id-{channel}"


def item(identifier, category="restocks", stores=(), hot=False, source_id="reddit-deals", published=None):
    return {
        "id": identifier,
        "title": f"Item {identifier}",
        "link": "https://x",
        "source": "S",
        "source_id": source_id,
        "publisher": "",
        "published": published or iso(NOW - timedelta(minutes=20)),
        "category": category,
        "stores": list(stores),
        "hot": hot,
    }


def window(identifier="w", start=None, remind=True, minutes=45):
    start = start or NOW + timedelta(minutes=30)
    return {
        "key": f"{identifier}:day",
        "id": identifier,
        "store": "Walmart",
        "title": "Drawing entry window",
        "status": "reported",
        "note": "n",
        "action": "Do the thing.",
        "link": "https://x",
        "repeat": "Every Wednesday",
        "remind": remind,
        "remind_minutes": minutes,
        "start": iso(start),
        "end": iso(start + timedelta(hours=1)),
    }


def product(identifier, profit, msrp=49.99, current=True):
    return {
        "id": identifier,
        "name": f"Product {identifier}",
        "msrp": msrp,
        "market": msrp + 40,
        "profit": profit,
        "current": current,
        "url": "https://t",
    }


def run(client, state, items=(), windows=(), events=(), products=(), now=NOW):
    discord_post.run(now, client, list(items), list(windows), list(events), [], list(products), state)


def is_ping(payload):
    return payload.get("content") == "@everyone" and payload["allowed_mentions"] == {"parse": ["everyone"]}


class RoutingTests(unittest.TestCase):
    def test_store_news_goes_to_that_store(self):
        self.assertEqual(discord_post.channels_for(item("a", stores=["Walmart"])), ["walmart"])

    def test_preorders_also_go_to_the_preorder_channel(self):
        self.assertEqual(discord_post.channels_for(item("a", "preorders", ["Target"])), ["target", "preorders"])

    def test_smaller_stores_share_a_channel(self):
        self.assertEqual(discord_post.channels_for(item("a", stores=["Best Buy", "Amazon"])), ["other-stores"])

    def test_restock_without_a_store(self):
        self.assertEqual(discord_post.channels_for(item("a")), ["other-stores"])

    def test_news_categories(self):
        self.assertEqual(discord_post.channels_for(item("a", "releases")), ["new-products"])
        self.assertEqual(discord_post.channels_for(item("a", "market")), ["market-prices"])
        self.assertEqual(discord_post.channels_for(item("a", "news")), ["pokemon-news"])

    def test_every_route_points_at_a_real_channel(self):
        keys = {channel["key"] for category in CONFIG["categories"] for channel in category["channels"]}
        routes = set(discord_post.GENERAL_CHANNEL.values()) | set(discord_post.STORE_CHANNEL.values())
        self.assertLessEqual(routes | {"other-stores", "drop-pings", "next-drops", "what-to-buy", "release-calendar"}, keys)


class PostingTests(unittest.TestCase):
    def test_first_run_posts_a_few_with_no_pings(self):
        items = [item(str(n), stores=["Walmart"], hot=True) for n in range(10)]
        posts, handled = discord_post.plan_item_posts(items, set(), True, NOW)
        self.assertEqual(len(posts), 3)
        self.assertEqual(len(handled), 10)
        self.assertFalse(any(ping for _, _, ping in posts))

    def test_fresh_alert_pings_once(self):
        posts, _ = discord_post.plan_item_posts([item("a", stores=["Walmart"], hot=True)], set(), False, NOW)
        self.assertEqual([(channel, ping) for channel, _, ping in posts], [("walmart", False), ("drop-pings", True)])

    def test_news_articles_never_ping(self):
        posts, _ = discord_post.plan_item_posts([item("a", hot=True, source_id="gn-restock")], set(), False, NOW)
        self.assertFalse(any(ping for _, _, ping in posts))

    def test_old_alerts_never_ping(self):
        old = iso(NOW - timedelta(hours=5))
        posts, _ = discord_post.plan_item_posts([item("a", hot=True, published=old)], set(), False, NOW)
        self.assertFalse(any(ping for _, _, ping in posts))

    def test_pings_are_capped_per_run(self):
        items = [item(str(n), hot=True) for n in range(8)]
        posts, _ = discord_post.plan_item_posts(items, set(), False, NOW)
        self.assertEqual(sum(1 for _, _, ping in posts if ping), discord_post.MAX_PINGS_PER_RUN)

    def test_posted_items_are_skipped(self):
        self.assertEqual(discord_post.plan_item_posts([item("a")], {"a"}, False, NOW), ([], set()))

    def test_progress_is_kept_when_a_post_fails(self):
        state = {"initialized": True}
        with self.assertRaises(discord_api.DiscordError):
            run(FakeClient(fail=True), state, items=[item("a")])
        self.assertEqual(state["posted_items"], [])


class BoardTests(unittest.TestCase):
    def test_boards_only_change_when_their_content_does(self):
        state, client = {"initialized": True}, FakeClient()
        windows = [window()]
        run(client, state, windows=windows)
        first = client.upserts
        run(client, state, windows=windows)
        self.assertEqual(client.upserts, first)
        run(client, state, windows=[window(start=NOW + timedelta(hours=2))])
        self.assertEqual(client.upserts, first + 1)

    def test_board_shows_each_window_once_with_a_live_countdown(self):
        windows = [window("a"), window("a", NOW + timedelta(days=1)), window("b", NOW + timedelta(days=2))]
        embed = discord_post.next_drops_board(windows, [])["embeds"][0]
        self.assertIn(":R>", embed["description"])
        self.assertEqual(len(embed["fields"]), 1)
        self.assertEqual(embed["fields"][0]["value"].count("Drawing entry window"), 1)

    def test_date_ranges_show_both_ends(self):
        event = {"title": "T", "date": "2026-12-28", "end": "2026-12-30", "status": "estimate"}
        self.assertIn("Dec 28 to Dec 30", discord_post.describe_event(event))

    def test_what_to_buy_lists_winners_and_losers(self):
        embed = discord_post.what_to_buy_board([product(1, 100), product(2, -30), product(3, 500, current=False)])["embeds"][0]
        text = " ".join(field["value"] for field in embed["fields"])
        self.assertIn("Product 1", text)
        self.assertIn("Product 2", text)
        self.assertNotIn("Product 3", text)

    def test_embeds_stay_within_discord_limits(self):
        windows = [window(str(n), NOW + timedelta(hours=n + 1)) for n in range(14)]
        events = [{"id": str(n), "title": "T" * 240, "date": "2026-12-04", "status": "reported", "msrp": 9.99} for n in range(40)]
        products = [product(n, 100 - n) for n in range(60)]
        boards = [
            discord_post.next_drops_board(windows, events),
            discord_post.calendar_board(events, []),
            discord_post.what_to_buy_board(products),
            discord_post.morning_payload(windows),
        ]
        for board in boards:
            embed = board["embeds"][0]
            self.assertLessEqual(len(embed.get("description", "")), 4096)
            self.assertTrue(all(len(field["value"]) <= 1024 for field in embed.get("fields", [])))
            total = len(embed["title"]) + len(embed.get("description", "")) + sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", []))
            self.assertLessEqual(total, 6000)


class PingTests(unittest.TestCase):
    def test_reminder_only_fires_inside_its_lead_time(self):
        target = window(start=NOW + timedelta(minutes=90))
        self.assertEqual(discord_post.due_reminders([target], NOW, set()), [])
        self.assertEqual(len(discord_post.due_reminders([window()], NOW, set())), 1)
        self.assertEqual(discord_post.due_reminders([window()], NOW, {"window:w:day"}), [])

    def test_reminder_pings_on_a_normal_run(self):
        client = FakeClient()
        run(client, {"initialized": True}, windows=[window()])
        pings = [payload for channel, payload in client.sent if channel == "drop-pings"]
        self.assertTrue(pings and all(is_ping(payload) for payload in pings))

    def test_first_run_is_silent(self):
        client = FakeClient()
        run(client, {}, windows=[window()])
        self.assertEqual(client.sent, [])

    def test_morning_heads_up_covers_the_next_day_once(self):
        state, client = {"initialized": True}, FakeClient()
        windows = [window(start=NOW + timedelta(hours=5))]
        run(client, state, windows=windows)
        run(client, state, windows=windows)
        titles = [payload["embeds"][0]["title"] for _, payload in client.sent]
        self.assertEqual(titles.count("🔔 Drops in the next 24 hours"), 1)

    def test_morning_heads_up_waits_for_seven_eastern(self):
        early = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)
        client = FakeClient()
        run(client, {"initialized": True}, windows=[window(start=early + timedelta(hours=8))], now=early)
        self.assertEqual(client.sent, [])

    def test_changed_date_is_announced_once(self):
        event = {"id": "e", "title": "T", "date": "2026-12-04", "status": "reported", "sources": []}
        state, client = {"initialized": True}, FakeClient()
        run(client, state, events=[event])
        run(client, state, events=[event])
        run(client, state, events=[{**event, "date": "2026-12-11"}])
        titles = [payload["embeds"][0]["title"] for channel, payload in client.sent if channel == "release-calendar"]
        self.assertEqual(titles, ["New date: T", "Date update: T"])


class ClientSafetyTests(unittest.TestCase):
    class Response:
        status_code = 200
        content = b'{"id": "m1"}'

        def json(self):
            return {"id": "m1"}

    def test_webhook_failures_never_expose_the_url(self):
        import requests

        class Session:
            def request(self, *args, **kwargs):
                raise requests.ConnectionError("https://discord.com/api/v10/webhooks/1/SECRET failed")

        with self.assertRaises(discord_api.DiscordError) as caught:
            discord_api.Discord("token", Session()).post({"id": "1", "token": "SECRET"}, {"content": "x"})
        self.assertNotIn("SECRET", str(caught.exception))

    def test_feed_text_cannot_ping_anyone(self):
        captured = {}
        response = self.Response()

        class Session:
            def request(self, method, url, timeout, **kwargs):
                captured.update(kwargs["json"])
                return response

        discord_api.Discord("t", Session()).post({"id": "1", "token": "x"}, {"content": "@everyone"})
        self.assertEqual(captured["allowed_mentions"], {"parse": []})

    def test_deleted_board_message_is_posted_again(self):
        class Api:
            def edit(self, webhook, message_id, payload):
                raise discord_api.DiscordError("gone", discord_api.NOT_FOUND)

            def post(self, webhook, payload):
                return {"id": "new"}

        client = discord_post.WebhookClient(Api(), {"next-drops": {"id": "1", "token": "x"}})
        self.assertEqual(client.upsert("next-drops", {}, "old"), "new")

    def test_other_edit_errors_are_not_hidden(self):
        class Api:
            def edit(self, webhook, message_id, payload):
                raise discord_api.DiscordError("forbidden", 403)

        client = discord_post.WebhookClient(Api(), {"next-drops": {"id": "1", "token": "x"}})
        with self.assertRaises(discord_api.DiscordError):
            client.upsert("next-drops", {}, "old")

    def test_long_guides_are_split_under_the_limit(self):
        chunks = discord_api.split_message("\n\n".join("word " * 120 for _ in range(10)))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1900 for chunk in chunks))


class FakeSetupApi:
    def __init__(self):
        self.channels_list = []
        self.hooks = {}
        self.posts = []
        self.positions = None
        self.next_id = 100

    def channels(self, guild_id):
        return [dict(channel) for channel in self.channels_list]

    def create_channel(self, guild_id, body):
        self.next_id += 1
        channel = {"id": str(self.next_id), **body}
        self.channels_list.append(channel)
        return dict(channel)

    def update_channel(self, channel_id, body):
        raise AssertionError("nothing should need updating on a repeat run")

    def set_positions(self, guild_id, positions):
        self.positions = positions

    def channel_webhooks(self, channel_id):
        return self.hooks.get(channel_id, [])

    def create_webhook(self, channel_id, name):
        hook = {"id": f"w{channel_id}", "token": "t", "name": name, "channel_id": channel_id}
        self.hooks.setdefault(channel_id, []).append(hook)
        return hook

    def has_messages(self, channel_id):
        return any(hook_channel == channel_id for hook_channel, _ in self.posts)

    def post(self, webhook, payload):
        self.posts.append((webhook["channel_id"], payload))
        return {"id": "m"}


class SetupTests(unittest.TestCase):
    def build(self):
        api = FakeSetupApi()
        api.channels_list.append({"id": "1", "type": 4, "name": "Text Channels"})
        result = discord_setup.build_server(api, "guild", CONFIG)
        return api, result

    def test_builds_every_channel_as_read_only(self):
        api, result = self.build()
        text = [channel for channel in api.channels_list if channel["type"] == 0]
        self.assertEqual(len(text), result["channels"])
        for channel in text:
            self.assertEqual(channel["permission_overwrites"][0]["deny"], "2048")

    def test_guides_get_clickable_links_and_fit_in_a_message(self):
        api, result = self.build()
        self.assertGreater(result["guides_posted"], 0)
        for _, payload in api.posts:
            self.assertNotIn("{{", payload["content"])
            self.assertLessEqual(len(payload["content"]), 1900)
        self.assertTrue(any("<#" in payload["content"] for _, payload in api.posts))

    def test_setup_sends_exactly_one_test_ping(self):
        api, _ = self.build()
        pings = [payload for _, payload in api.posts if payload.get("allowed_mentions") == {"parse": ["everyone"]}]
        self.assertEqual(len(pings), 1)
        self.assertTrue(pings[0]["content"].startswith("@everyone"))
        discord_setup.build_server(api, "guild", CONFIG)
        pings_after = [payload for _, payload in api.posts if payload.get("allowed_mentions") == {"parse": ["everyone"]}]
        self.assertEqual(len(pings_after), 1)

    def test_running_again_changes_nothing(self):
        api, _ = self.build()
        channels, posts = len(api.channels_list), len(api.posts)
        discord_setup.build_server(api, "guild", CONFIG)
        self.assertEqual((len(api.channels_list), len(api.posts)), (channels, posts))

    def test_new_categories_come_first(self):
        api, _ = self.build()
        ordered = [entry["id"] for entry in sorted(api.positions, key=lambda entry: entry["position"])]
        self.assertEqual(ordered[-1], "1")

    def test_channels_are_unique_and_each_guide_exists(self):
        keys, names = [], []
        for category in CONFIG["categories"]:
            for channel in category["channels"]:
                keys.append(channel["key"])
                names.append(discord_api.normalized(channel["name"]))
                self.assertLessEqual(len(channel["name"]), 100)
                self.assertLessEqual(len(channel.get("topic", "")), 1024)
                if channel.get("static"):
                    self.assertTrue((common.CONFIG_DIR / "discord" / channel["static"]).exists(), channel["static"])
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(names), len(set(names)))

    def test_emoji_do_not_change_channel_matching(self):
        self.assertEqual(discord_api.normalized("🔔-drop-pings"), discord_api.normalized("drop pings"))


if __name__ == "__main__":
    unittest.main()
