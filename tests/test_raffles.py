import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from pipeline import classify, common, discord_post, market, raffles
from pipeline.common import iso

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
COSTS = common.load_json(common.CONFIG_DIR / "assumptions.json")
NEWS = {"id": "n", "name": "News", "default": "news", "google_news": True}
ROWS = {"Good Box": {"name": "Good Box", "market": 150.0, "ship": "etb"}, "Weak Box": {"name": "Weak Box", "market": 60.0, "ship": "etb"}}


class FakeClient:
    def __init__(self):
        self.sent = []
        self.boards = {}

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.boards[channel] = payload
        return message_id or "id"


def entry(identifier="a", name="Good Box", price=49.99):
    item = {"id": identifier, "store": "Best Buy", "title": name, "price": price, "match": name, "closes": "", "link": "https://x"}
    return raffles.priced_item(item, ROWS, COSTS)


def news(identifier, stores=("Walmart",), category="raffles", hot=True, published=None, source_id="gn-raffles"):
    return {
        "id": identifier,
        "title": f"Story {identifier}",
        "link": "https://x",
        "source": "S",
        "source_id": source_id,
        "publisher": "",
        "published": published or iso(NOW - timedelta(hours=1)),
        "category": category,
        "stores": list(stores),
        "hot": hot,
    }


class ClassifyTests(unittest.TestCase):
    RULES = classify.load_rules()

    def decide(self, title):
        return classify.classify(title, "https://x", NEWS, self.RULES)

    def test_drawing_news_is_a_raffle(self):
        result = self.decide("Walmart sets new Pokémon TCG drawing for 30th Celebration")
        self.assertEqual(result["category"], "raffles")
        self.assertTrue(result["hot"])

    def test_invites_are_raffles(self):
        self.assertEqual(self.decide("Best Buy Pokémon TCG invites are live for Delta Reign")["category"], "raffles")

    def test_closed_drawing_does_not_ping(self):
        self.assertFalse(self.decide("Walmart Pokémon TCG drawing just closed")["hot"])

    def test_raffles_reach_the_store_and_the_raffle_channel(self):
        self.assertEqual(discord_post.channels_for(news("a")), ["walmart", "raffles"])


class PingTests(unittest.TestCase):
    def test_raffle_news_can_ping_from_any_source(self):
        posts, _ = discord_post.plan_item_posts([news("a")], set(), False, NOW, set())
        self.assertIn("drop-pings", [channel for channel, _, _ in posts])

    def test_old_raffle_news_does_not_ping(self):
        old = iso(NOW - discord_post.RAFFLE_PING_FRESH_FOR - timedelta(hours=1))
        posts, _ = discord_post.plan_item_posts([news("a", published=old)], set(), False, NOW, set())
        self.assertNotIn("drop-pings", [channel for channel, _, _ in posts])

    def test_several_stories_about_one_store_ping_once_a_day(self):
        keys = set()
        posts, _ = discord_post.plan_item_posts([news("a"), news("b")], set(), False, NOW, keys)
        self.assertEqual([channel for channel, _, _ in posts].count("drop-pings"), 1)
        posts, _ = discord_post.plan_item_posts([news("c")], set(), False, NOW, keys)
        self.assertEqual([channel for channel, _, _ in posts].count("drop-pings"), 0)

    def test_different_stores_each_get_a_ping(self):
        posts, _ = discord_post.plan_item_posts([news("a"), news("b", stores=("Target",))], set(), False, NOW, set())
        self.assertEqual([channel for channel, _, _ in posts].count("drop-pings"), 2)


class BoardTests(unittest.TestCase):
    def board(self, items):
        return discord_post.raffles_board({"programs": common.load_json(common.DATA_DIR / "raffles.json")["programs"], "open": items})["embeds"][0]

    def test_profitable_sign_up_is_listed_with_its_profit(self):
        self.assertIn("**+$", self.board([entry()])["fields"][0]["value"])

    def test_unprofitable_sign_up_stays_off(self):
        self.assertIn("Nothing open right now", self.board([entry(name="Weak Box")])["fields"][0]["value"])

    def test_best_profit_comes_first(self):
        value = self.board([entry("a", price=80), entry("b", price=40)])["fields"][0]["value"]
        self.assertLess(value.index("$40.00"), value.index("$80.00"))

    def test_every_program_is_explained(self):
        names = [field["name"] for field in self.board([])["fields"]]
        for store in ("Best Buy", "Walmart", "Target", "Amazon", "Pokémon Center"):
            self.assertTrue(any(name.startswith(store) for name in names), store)

    def test_fits_discord_limits(self):
        embed = self.board([entry(str(n), name="Good Box") for n in range(50)])
        self.assertLessEqual(len(embed["fields"]), 25)
        self.assertTrue(all(len(f["value"]) <= 1024 for f in embed["fields"]))
        total = len(embed["title"]) + len(embed["description"]) + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
        self.assertLessEqual(total, 6000)


class AnnounceTests(unittest.TestCase):
    def run_with(self, state, items):
        client = FakeClient()
        discord_post.apply_raffles(client, state, {"programs": [], "open": items})
        return client

    def test_first_run_only_records(self):
        state = {}
        self.assertEqual(self.run_with(state, [entry()]).sent, [])
        self.assertEqual(state["raffles_announced"], ["a"])

    def test_new_profitable_sign_up_pings_once(self):
        state = {"raffles_announced": []}
        self.assertEqual(self.run_with(state, [entry()]).sent[0][0], "drop-pings")
        self.assertEqual(self.run_with(state, [entry()]).sent, [])

    def test_unprofitable_sign_up_does_not_ping(self):
        self.assertEqual(self.run_with({"raffles_announced": []}, [entry(name="Weak Box")]).sent, [])


class DataTests(unittest.TestCase):
    DATA = common.load_json(common.DATA_DIR / "raffles.json")

    def test_programs_are_complete(self):
        for program in self.DATA["programs"]:
            for field in ("store", "name", "how", "when", "link", "sources"):
                self.assertTrue(program[field], f"{program['id']} needs {field}")

    def test_open_items_are_complete(self):
        ids = [item["id"] for item in self.DATA["open"]]
        self.assertEqual(len(ids), len(set(ids)))
        for item in self.DATA["open"]:
            self.assertGreater(item["price"], 0)
            self.assertTrue(item["link"].startswith("https://"))
            if item["closes"]:
                date.fromisoformat(item["closes"])


class CoverageTests(unittest.TestCase):
    def test_older_sets_are_tracked(self):
        groups = [{"groupId": 1, "name": name} for name in ("SV: Scarlet & Violet 151", "SWSH12: Silver Tempest", "Celebrations", "ME01: Mega Evolution")]
        self.assertEqual(len(market.modern_groups(groups)), 4)

    def test_promo_and_energy_groups_stay_out(self):
        groups = [{"groupId": 1, "name": "ME: Mega Evolution Promo"}, {"groupId": 2, "name": "SV: Scarlet & Violet Energies"}]
        self.assertEqual(market.modern_groups(groups), [])

    def test_set_prefix_is_removed(self):
        self.assertEqual(market.set_label("SWSH12: Silver Tempest"), "Silver Tempest")

    def test_big_collections_get_their_real_price(self):
        _, rules = market.compile_rules(common.load_json(common.CONFIG_DIR / "products.json"))
        price = lambda name: market.match_rule(common.fold(name), rules)["msrp"]
        self.assertEqual(price("Prismatic Evolutions Super-Premium Collection"), 89.99)
        self.assertEqual(price("Prismatic Evolutions Premium Figure Collection"), 69.99)
        self.assertEqual(price("Charizard ex Ultra-Premium Collection"), 119.99)
        self.assertEqual(price("30th Celebration Ultra-Premium Collection [Night]"), 179.99)

    def test_older_sets_get_ebay_prices_too(self):
        searched = []

        class FakeEbay:
            def __init__(self, *args):
                pass

            def authenticate(self):
                return True

            def search(self, query):
                searched.append(query)
                return []

        rows = [{"name": "New Box", "market": 50, "current": True}, {"name": "Old Box", "market": 50, "current": False}]
        keys = {"EBAY_CLIENT_ID": "a", "EBAY_CLIENT_SECRET": "b"}
        with mock.patch.dict("os.environ", keys), mock.patch.object(market, "EbayClient", FakeEbay), mock.patch.object(market.time, "sleep"):
            market.add_ebay_asks(rows, None)
        self.assertEqual(len(searched), 2)


class NewProductTests(unittest.TestCase):
    def product(self, identifier, current):
        return {"id": identifier, "name": f"P{identifier}", "msrp": 10, "market": 90, "profit": 50, "current": current, "url": "https://x"}

    def test_older_sets_joining_the_tracker_are_not_announced(self):
        client = FakeClient()
        state = {"initialized": True, "known_products": []}
        discord_post.run(NOW, client, [], [], [], [], [self.product(1, False), self.product(2, True)], state)
        titles = [payload["embeds"][0]["title"] for channel, payload in client.sent if channel == "new-products"]
        self.assertEqual(titles, ["New sealed listing: P2"])


if __name__ == "__main__":
    unittest.main()
