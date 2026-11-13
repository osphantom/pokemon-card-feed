import unittest
from datetime import datetime, timezone

from pipeline import signups

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
COSTS = {
    "fee_rate": 0.1325, "fee_cap": 7500, "over_rate": 0.0235, "per_order_small": 0.30,
    "per_order_large": 0.40, "per_order_cutoff": 10, "top_rated": False, "buyer_tax": 0.07,
    "your_tax": 0.07, "retail_discount": 0, "sell_pct": 1, "supplies": 1,
    "shipping": {"etb": 9.5, "upc": 18, "box": 12},
}
PRODUCTS = [
    {"name": "30th Celebration Ultra-Premium Collection [Night]", "market": 390.71, "msrp": 179.99, "ship": "upc", "current": True},
    {"name": "30th Celebration Ultra-Premium Collection [Day]", "market": 450.00, "msrp": 179.99, "ship": "upc", "current": True},
    {"name": "Prismatic Evolutions Elite Trainer Box", "market": 114.61, "msrp": 49.99, "ship": "etb", "current": True},
    {"name": "Delta Reign Booster Box", "market": 120.00, "msrp": 160.99, "ship": "box", "current": True},
]
PROGRAMS = [{"store": "Walmart", "how": "Sign in and enter the drawing during the window."}]


def item(category, title, stores, published="2026-11-12T00:00:00Z"):
    return {"category": category, "title": title, "link": "https://x", "stores": stores, "published": published}


class MatchTests(unittest.TestCase):
    def test_matches_the_cheapest_fitting_version(self):
        product = signups.match_product("Pokémon 30th Celebration Ultra-Premium Collection preorder", PRODUCTS)
        self.assertEqual(product["name"], "30th Celebration Ultra-Premium Collection [Night]")

    def test_no_specific_product_no_match(self):
        self.assertIsNone(signups.match_product("How to get cheap Pokémon cards from Walmart lotteries", PRODUCTS))


class CollectTests(unittest.TestCase):
    def run_collect(self, items):
        return signups.collect(items, PRODUCTS, PROGRAMS, NOW, costs=COSTS)

    def test_prices_a_raffle_and_a_preorder(self):
        found = self.run_collect([
            item("raffles", "Walmart Pokémon drawing — 30th Celebration Ultra-Premium Collection UPCs", ["Walmart"]),
            item("preorders", "Pokémon TCG 30th Celebration Ultra-Premium Collection preorders live at Amazon", ["Amazon"]),
        ])
        self.assertEqual(len(found), 2)
        self.assertTrue(all(s["profit"] > 100 for s in found))
        self.assertEqual({s["kind"] for s in found}, {"raffles", "preorders"})

    def test_how_comes_from_the_store_program(self):
        found = self.run_collect([item("raffles", "Walmart drawing 30th Celebration Ultra-Premium Collection", ["Walmart"])])
        self.assertIn("enter the drawing", found[0]["how"].lower())

    def test_unprofitable_is_dropped(self):
        found = self.run_collect([item("preorders", "Delta Reign Booster Box preorder at Best Buy", ["Best Buy"])])
        self.assertEqual(found, [])

    def test_stale_news_is_dropped(self):
        found = self.run_collect([item("raffles", "Walmart drawing 30th Celebration Ultra-Premium Collection", ["Walmart"], published="2026-10-01T00:00:00Z")])
        self.assertEqual(found, [])

    def test_non_signup_categories_ignored(self):
        found = self.run_collect([item("news", "30th Celebration Ultra-Premium Collection review", ["Walmart"])])
        self.assertEqual(found, [])

    def test_one_per_product_store_kind(self):
        found = self.run_collect([
            item("raffles", "Walmart drawing 30th Celebration Ultra-Premium Collection UPC", ["Walmart"]),
            item("raffles", "ANOTHER Walmart 30th Celebration Ultra-Premium Collection drawing", ["Walmart"]),
        ])
        self.assertEqual(len(found), 1)

    def test_same_product_collapses_and_prefers_a_named_store(self):
        found = self.run_collect([
            item("preorders", "30th Celebration Ultra-Premium Collection preorder guide", []),
            item("preorders", "30th Celebration Ultra-Premium Collection preorder live at Amazon", ["Amazon"]),
        ])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["store"], "Amazon")

    def test_splits_into_raffle_opens_and_preorders(self):
        found = self.run_collect([
            item("raffles", "Walmart drawing 30th Celebration Ultra-Premium Collection", ["Walmart"]),
            item("preorders", "30th Celebration Ultra-Premium Collection preorder at Amazon", ["Amazon"]),
        ])
        opens = signups.as_open_raffles(found)
        pres = signups.preorder_signups(found)
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["price"], 179.99)
        self.assertEqual(opens[0]["store"], "Walmart")
        self.assertEqual(len(pres), 1)
        self.assertEqual(pres[0]["store"], "Amazon")


class FakeClient:
    def __init__(self):
        self.sent = []
        self.boards = {}

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.boards[channel] = payload
        return message_id or "id"


class BoardTests(unittest.TestCase):
    SAMPLE = [{"kind": "preorders", "store": "Amazon", "product": "30th Celebration Ultra-Premium Collection",
               "market": 346.0, "msrp": 179.99, "profit": 85.0, "title": "t", "link": "https://x", "how": None,
               "published": "2026-11-12T00:00:00Z"}]

    def test_board_is_a_valid_embed(self):
        from pipeline import discord_post
        import json
        board = discord_post.preorders_board(self.SAMPLE)
        self.assertLess(len(json.dumps(board, ensure_ascii=False)), 6000)
        self.assertIn("+$85.00", board["embeds"][0]["fields"][0]["value"])

    def test_first_run_is_silent_then_new_ones_ping(self):
        from pipeline import discord_post
        client, state = FakeClient(), {}
        discord_post.apply_preorders(client, state, self.SAMPLE)
        self.assertEqual(client.sent, [])  # first run records, no ping
        self.assertIn("preorders", client.boards)
        client2, state2 = FakeClient(), {"preorders_announced": []}
        discord_post.apply_preorders(client2, state2, self.SAMPLE)
        self.assertEqual([channel for channel, _ in client2.sent], ["drop-pings"])


if __name__ == "__main__":
    unittest.main()
