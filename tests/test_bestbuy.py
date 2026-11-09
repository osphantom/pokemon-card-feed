import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

from pipeline import bestbuy, discord_api, discord_post
from pipeline.common import iso

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
KEY = "secret-key-123"
OWNER = "42"
AREA = "Maryland"
ETB = "Pokémon - Trading Card Game: Delta Reign Elite Trainer Box"
discord_api.time.sleep = lambda seconds: None


def listing(sku=111, name=ETB, price=49.99, online=True, in_store=True, orderable="Available"):
    return {
        "sku": sku,
        "name": name,
        "salePrice": price,
        "onlineAvailability": online,
        "inStoreAvailability": in_store,
        "orderable": orderable,
        "url": f"https://api.bestbuy.com/click/-/{sku}/pdp",
        "addToCartUrl": f"https://api.bestbuy.com/click/-/{sku}/cart",
        "thumbnailImage": f"https://img.example/{sku}.jpg",
    }


def store(identifier="10", name="Columbia", state="MD", low=False):
    return {"storeID": identifier, "name": name, "city": name, "state": state, "lowStock": low, "distance": 3.2}


def product(sku="111", online=True, stores=(), price=49.99, normal=49.99, name="Delta Reign ETB"):
    return {
        "sku": sku,
        "name": name,
        "price": price,
        "normal": normal,
        "online": online,
        "preorder": False,
        "url": "https://x/pdp",
        "cart": "https://x/cart",
        "image": None,
        "stores": list(stores),
    }


COLUMBIA = {"id": "10", "name": "Columbia", "low": False}
TOWSON = {"id": "20", "name": "Towson", "low": True}


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.body = body

    def json(self):
        return self.body


class Session:
    def __init__(self, products=(), stores=None, status=200, error=None):
        self.headers = {}
        self.calls = []
        self.products = list(products)
        self.stores = stores or {}
        self.status = status
        self.error = error

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        if self.error:
            raise self.error
        if self.status != 200:
            return Response(self.status)
        if "/stores.json" in url:
            sku = int(url.split("/products/")[1].split("/")[0])
            return Response(200, {"ispuEligible": True, "stores": self.stores.get(sku, [])})
        return Response(200, {"totalPages": 1, "products": self.products})


class FakeClient:
    def __init__(self):
        self.posts = []
        self.deleted = []
        self.count = 0

    def upsert(self, channel, payload, message_id):
        self.posts.append((channel, payload, message_id))
        self.count += 1
        return message_id or f"m{self.count}"

    def delete(self, channel, message_id):
        self.deleted.append((channel, message_id))

    def alerts(self):
        return [payload for _, payload, message_id in self.posts if "title" in payload["embeds"][0] and "right now" not in payload["embeds"][0]["title"]]


def fetch(session):
    return bestbuy.fetch_stock(KEY, session, pause=0)


class FetchTests(unittest.TestCase):
    def test_reads_online_and_maryland_stock(self):
        session = Session([listing()], {111: [store(), store("99", "Dover", "DE")]})
        with contextlib.redirect_stdout(io.StringIO()):
            stock = fetch(session)
        self.assertEqual(len(stock), 1)
        self.assertTrue(stock[0]["online"])
        self.assertEqual(stock[0]["normal"], 49.99)
        self.assertEqual([s["name"] for s in stock[0]["stores"]], ["Columbia"])

    def test_skips_things_that_are_not_sealed_english_cards(self):
        names = [
            "Ultra PRO - Pokémon Trading Card Game Sleeves",
            "Pokémon - Trading Card Game: Japanese Booster Box",
            "Pokémon Legends - Nintendo Switch",
            "Pikachu Plush Trading Card Holder",
        ]
        session = Session([listing(i, name) for i, name in enumerate(names)])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fetch(session), [])

    def test_keeps_sealed_products_with_no_known_price(self):
        session = Session([listing(name="Pokémon - Trading Card Game: Holiday Calendar")])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(fetch(session)[0]["normal"])

    def test_binder_collection_is_sealed_but_a_binder_is_not(self):
        session = Session([listing(1, "Pokémon Trading Card Game: Binder Collection"), listing(2, "Pokémon Trading Card Game Binder")])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual([p["sku"] for p in fetch(session)], ["1"])

    def test_only_checks_stores_for_items_sold_in_stores(self):
        session = Session([listing(in_store=False)])
        with contextlib.redirect_stdout(io.StringIO()):
            fetch(session)
        self.assertEqual(len(session.calls), 1)

    def test_identifies_itself_honestly(self):
        session = Session([])
        with contextlib.redirect_stdout(io.StringIO()):
            fetch(session)
        self.assertNotIn("Mozilla", session.headers["User-Agent"])

    def test_a_refusal_stops_the_check_without_retrying(self):
        session = Session(status=403)
        with self.assertRaises(bestbuy.StockCheckFailed):
            fetch(session)
        self.assertEqual(len(session.calls), 1)

    def test_the_key_never_reaches_the_error_text(self):
        leaky = requests.ConnectionError(f"Max retries with url: /v1/products?apiKey={KEY}")
        with self.assertRaises(bestbuy.StockCheckFailed) as caught:
            fetch(Session(error=leaky))
        self.assertNotIn(KEY, str(caught.exception))

    def test_logs_only_counts(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fetch(Session([listing()], {111: [store()]}))
        self.assertNotIn("Delta", out.getvalue())
        self.assertNotIn("111", out.getvalue())


class RunTests(unittest.TestCase):
    def run_once(self, stock, state, client=None, now=NOW):
        client = client or FakeClient()
        bestbuy.run(now, client, state, stock, AREA, OWNER)
        return client

    def test_first_check_only_records(self):
        state = {}
        client = self.run_once([product()], state)
        self.assertEqual(client.alerts(), [])
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(len(state["facts"]), 1)

    def test_back_online_pings_the_owner_by_name(self):
        state = {"facts": []}
        client = self.run_once([product()], state)
        alert = client.alerts()[0]
        self.assertEqual(alert["content"], f"<@{OWNER}>")
        self.assertEqual(alert["allowed_mentions"], {"users": [OWNER]})
        self.assertIn("Back online", alert["embeds"][0]["title"])
        self.assertIn("Add to cart", alert["embeds"][0]["fields"][-1]["value"])

    def test_new_store_is_news_even_if_already_online(self):
        state = {}
        self.run_once([product()], state)
        client = self.run_once([product(stores=[COLUMBIA])], state, now=NOW + timedelta(minutes=15))
        embed = client.alerts()[0]["embeds"][0]
        self.assertIn(f"In stock in {AREA}", embed["title"])

    def test_no_change_means_no_alert(self):
        state = {}
        self.run_once([product(stores=[COLUMBIA])], state)
        client = self.run_once([product(stores=[COLUMBIA])], state, now=NOW + timedelta(minutes=15))
        self.assertEqual(client.alerts(), [])

    def test_stock_that_blinks_does_not_ping_again_right_away(self):
        state = {"facts": []}
        self.run_once([product()], state)
        self.run_once([], state, now=NOW + timedelta(minutes=15))
        client = self.run_once([product()], state, now=NOW + timedelta(minutes=30))
        self.assertEqual(client.alerts(), [])

    def test_stock_that_returns_much_later_pings_again(self):
        state = {"facts": []}
        self.run_once([product()], state)
        self.run_once([], state, now=NOW + timedelta(minutes=15))
        client = self.run_once([product()], state, now=NOW + bestbuy.REALERT_AFTER + timedelta(minutes=1))
        self.assertEqual(len(client.alerts()), 1)

    def test_overpriced_listing_posts_without_a_ping(self):
        client = self.run_once([product(price=120.00)], {"facts": []})
        alert = client.alerts()[0]
        self.assertNotIn("content", alert)
        self.assertIn("normal $49.99", alert["embeds"][0]["fields"][0]["value"])

    def test_big_drop_fits_discord_limits_and_pings_once(self):
        stock = [product(str(n), stores=[COLUMBIA, TOWSON] * 30, name="A very long product name " * 12) for n in range(30)]
        client = self.run_once(stock, {"facts": []})
        alerts = client.alerts()
        self.assertEqual(sum("content" in alert for alert in alerts), 1)
        self.assertEqual(sum(len(alert["embeds"]) for alert in alerts), bestbuy.MAX_ALERTS)
        for alert in alerts:
            self.assertLessEqual(len(alert["embeds"]), 10)
            total = 0
            for embed in alert["embeds"]:
                self.assertLessEqual(len(embed["title"]), 256)
                total += len(embed["title"]) + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"]) + len(embed["footer"]["text"])
                self.assertTrue(all(len(f["value"]) <= 1024 for f in embed["fields"]))
            self.assertLessEqual(total, 6000)

    def test_big_list_fits_discord_limits(self):
        stock = [product(str(n), stores=[COLUMBIA, TOWSON] * 30, name="A very long product name " * 12) for n in range(200)]
        embed = bestbuy.board(stock, AREA, NOW)["embeds"][0]
        self.assertTrue(all(len(f["value"]) <= 1024 for f in embed["fields"]))

    def test_long_list_never_cuts_a_link_in_half(self):
        lines = [f"[Product {n}](https://x/{n})" for n in range(200)]
        text = bestbuy.fit_lines(lines)
        self.assertLessEqual(len(text), 1024)
        self.assertEqual(text.count("["), text.count("]"))
        self.assertRegex(text, r"…and \d+ more$")

    def test_empty_list_says_so(self):
        self.assertEqual(bestbuy.fit_lines([]), "Nothing sealed right now.")

    def test_every_post_credits_best_buy_with_its_logo(self):
        state = {"facts": []}
        client = self.run_once([product()], state)
        for _, payload, _ in client.posts:
            footer = payload["embeds"][0]["footer"]
            self.assertIn("Best Buy", footer["text"])
            self.assertEqual(footer["icon_url"], bestbuy.LOGO)

    def test_list_keeps_its_one_message(self):
        state = {}
        client = FakeClient()
        self.run_once([product()], state, client)
        first_id = state["board_id"]
        self.run_once([product()], state, client, now=NOW + timedelta(minutes=15))
        self.assertEqual(state["board_id"], first_id)
        self.assertEqual(client.posts[-1][2], first_id)

    def test_nothing_from_best_buy_is_saved(self):
        state = {"facts": []}
        self.run_once([product(sku="6601234", stores=[COLUMBIA], name="Delta Reign ETB")], state)
        saved = json.dumps(state)
        for private in ("6601234", "Delta", "Columbia", "49.99"):
            self.assertNotIn(private, saved)

    def test_a_failed_post_can_be_retried_without_doubling_what_was_sent(self):
        class FailsOnList(FakeClient):
            def upsert(self, channel, payload, message_id):
                if "right now" in payload["embeds"][0]["title"]:
                    raise discord_api.DiscordError("no")
                return super().upsert(channel, payload, message_id)

        state = {"facts": []}
        with self.assertRaises(discord_api.DiscordError):
            self.run_once([product()], state, FailsOnList())
        client = self.run_once([product()], state, now=NOW + timedelta(minutes=5))
        self.assertEqual(client.alerts(), [])


class ExpiryTests(unittest.TestCase):
    def test_old_alerts_are_deleted_and_new_ones_stay(self):
        client = FakeClient()
        state = {"alerts": [
            {"id": "old", "at": iso(NOW - bestbuy.KEEP_ALERTS_FOR)},
            {"id": "new", "at": iso(NOW - timedelta(hours=1))},
        ]}
        bestbuy.delete_expired(client, state, NOW)
        self.assertEqual(client.deleted, [(bestbuy.CHANNEL, "old")])
        self.assertEqual([alert["id"] for alert in state["alerts"]], ["new"])

    def test_alerts_go_well_before_best_buys_72_hour_limit(self):
        self.assertLess(bestbuy.KEEP_ALERTS_FOR, timedelta(hours=72))

    def test_alerts_are_tracked_for_deletion(self):
        state = {"facts": []}
        client = FakeClient()
        bestbuy.run(NOW, client, state, [product()], AREA, OWNER)
        self.assertEqual(len(state["alerts"]), 1)


class WebhookClientTests(unittest.TestCase):
    def client(self, api):
        return discord_post.WebhookClient(api, {"best-buy": {"id": "1", "token": "t"}})

    def test_a_message_already_gone_is_fine(self):
        api = mock.Mock()
        api.delete.side_effect = discord_api.DiscordError("gone", 404)
        self.client(api).delete("best-buy", "m")

    def test_other_delete_errors_are_raised(self):
        api = mock.Mock()
        api.delete.side_effect = discord_api.DiscordError("no", 403)
        with self.assertRaises(discord_api.DiscordError):
            self.client(api).delete("best-buy", "m")

    def test_delete_uses_the_webhook(self):
        class DiscordSession:
            def __init__(self):
                self.calls = []

            def request(self, method, url, timeout, **kwargs):
                self.calls.append((method, url))
                return mock.Mock(status_code=204, content=b"")

        session = DiscordSession()
        discord_api.Discord("token", session).delete({"id": "1", "token": "t"}, "99")
        self.assertEqual(session.calls[0][0], "DELETE")
        self.assertTrue(session.calls[0][1].endswith("/webhooks/1/t/messages/99"))


class SampleTests(unittest.TestCase):
    def test_sample_is_labeled_pings_and_cleans_up(self):
        client = FakeClient()
        bestbuy.post_sample(client, OWNER, AREA)
        payload = client.posts[0][1]
        self.assertIn("only a test", payload["content"])
        self.assertIn("TEST ALERT", payload["embeds"][0]["title"])
        self.assertEqual(payload["allowed_mentions"], {"users": [OWNER]})
        self.assertEqual(client.deleted, [(bestbuy.CHANNEL, "m2")])


class MainTests(unittest.TestCase):
    def main_with(self, env):
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out):
            code = bestbuy.main([])
        return code, out.getvalue()

    def test_no_key_is_a_quiet_skip(self):
        code, out = self.main_with({"DISCORD_BOT_TOKEN": "t", "DISCORD_GUILD_ID": "g"})
        self.assertEqual(code, 0)
        self.assertIn("No Best Buy key", out)


if __name__ == "__main__":
    unittest.main()
