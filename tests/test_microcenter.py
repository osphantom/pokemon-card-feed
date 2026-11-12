import unittest
from datetime import datetime, timezone

from tools import microcenter as mc

NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)
ASSUMPTIONS = {
    "sell_pct": 1, "fee_rate": 0.1325, "fee_cap": 7500, "over_rate": 0.0235, "top_rated": False,
    "per_order_small": 0.3, "per_order_large": 0.4, "per_order_cutoff": 10, "buyer_tax": 0.07,
    "your_tax": 0.07, "retail_discount": 0, "supplies": 1,
    "shipping": {"etb": 9.5, "tin": 8.5, "collection": 10, "bundle": 6.5},
}
MARKET = {"products": [
    {"name": "30th Celebration Elite Trainer Box", "market": 113.70, "msrp": 49.99, "ship": "etb"},
    {"name": "Prismatic Evolutions Elite Trainer Box", "market": 110.85, "msrp": 49.99, "ship": "etb"},
    {"name": "Journey Together Booster Bundle", "market": 40.0, "msrp": 26.94, "ship": "bundle"},
]}
BOOK = mc.build_book(MARKET, ASSUMPTIONS)


def tile(pid, name, price=49.99, stock="In stock"):
    return {"product_id": pid, "name": name, "price": price, "category": "Trading Cards", "brand": "Pokemon",
            "stock_text": stock, "rating": None, "reviews": None, "offer": None, "store_id": "125"}


CONFIG = {"store": {"id": "125", "name": "Parkville, MD"}, "min_profit": 20, "queries": ["pokemon", "pokemon etb"]}


class FilterTests(unittest.TestCase):
    def test_a_sealed_box_counts(self):
        self.assertTrue(mc.looks_sealed("Pokemon Trading Card Game 30th Celebration Elite Trainer Box"))

    def test_a_mini_tin_counts(self):
        self.assertTrue(mc.looks_sealed("Pokemon TCG Scarlet & Violet 151 Mini Tin"))

    def test_accessories_and_singles_are_dropped(self):
        for name in ("Pokemon Card Sleeves 65ct", "Pokemon Deck Box", "Pokemon Charizard Holo #4/102", "Pokemon Plush Pikachu"):
            self.assertFalse(mc.looks_sealed(name), name)

    def test_non_pokemon_is_dropped(self):
        self.assertFalse(mc.looks_sealed("Magic The Gathering Booster Box"))

    def test_stock_text_maps_to_a_state(self):
        self.assertEqual(mc.stock_of("In stock"), "in")
        self.assertEqual(mc.stock_of("Sold Out"), "out")
        self.assertEqual(mc.stock_of(""), "unknown")


class PriceTests(unittest.TestCase):
    def test_a_clear_name_matches_and_prices(self):
        value = mc.value_of("Pokemon Trading Card Game 30th Celebration Elite Trainer Box", 49.99, BOOK)
        self.assertEqual(value["match"], "30th Celebration Elite Trainer Box")
        self.assertGreater(value["profit"], 25)

    def test_profit_follows_the_micro_center_price(self):
        cheap = mc.value_of("Pokemon Prismatic Evolutions Elite Trainer Box", 44.99, BOOK)["profit"]
        dear = mc.value_of("Pokemon Prismatic Evolutions Elite Trainer Box", 59.99, BOOK)["profit"]
        self.assertGreater(cheap, dear)

    def test_an_unknown_set_is_not_priced(self):
        self.assertIsNone(mc.value_of("Pokemon Totally Made Up Set Elite Trainer Box", 49.99, BOOK))


class ScanTests(unittest.TestCase):
    def test_first_run_only_records(self):
        deals, arrivals, seen = mc.scan([tile("1", "Pokemon 30th Celebration Elite Trainer Box")], BOOK, 20, None)
        self.assertEqual((deals, arrivals), ([], []))
        self.assertEqual(seen["1"], {"stock": "in", "pays": True})

    def test_a_new_profitable_in_stock_item_is_a_deal(self):
        deals, arrivals, _ = mc.scan([tile("1", "Pokemon 30th Celebration Elite Trainer Box")], BOOK, 20, {})
        self.assertEqual(len(deals), 1)
        self.assertEqual(len(arrivals), 1)

    def test_a_restock_is_a_deal(self):
        prev = {"1": {"stock": "out", "pays": True}}
        deals, arrivals, _ = mc.scan([tile("1", "Pokemon 30th Celebration Elite Trainer Box")], BOOK, 20, prev)
        self.assertEqual(len(deals), 1)
        self.assertEqual(arrivals, [])

    def test_an_unprofitable_item_is_a_new_arrival_but_not_a_deal(self):
        deals, arrivals, _ = mc.scan([tile("9", "Pokemon Journey Together Booster Bundle", price=39.99)], BOOK, 20, {})
        self.assertEqual(deals, [])
        self.assertEqual(len(arrivals), 1)

    def test_staying_in_stock_is_not_a_fresh_deal(self):
        prev = {"1": {"stock": "in", "pays": True}}
        deals, _, _ = mc.scan([tile("1", "Pokemon 30th Celebration Elite Trainer Box")], BOOK, 20, prev)
        self.assertEqual(deals, [])

    def test_accessories_never_reach_the_scan(self):
        _, arrivals, seen = mc.scan([tile("7", "Pokemon Card Sleeves")], BOOK, 20, {})
        self.assertEqual(arrivals, [])
        self.assertEqual(seen, {})


class MessageTests(unittest.TestCase):
    def deal(self):
        deals, _, _ = mc.scan([tile("1", "Pokemon 30th Celebration Elite Trainer Box")], BOOK, 20, {})
        return mc.deal_message(deals[0], "Parkville, MD")

    def test_deal_pings_with_profit_and_link(self):
        message = self.deal()
        self.assertEqual(message["content"], "@everyone")
        body = message["embeds"][0]["description"]
        self.assertIn("+$", body)
        self.assertIn("microcenter.com/product/1", body)

    def test_arrivals_do_not_ping(self):
        _, arrivals, _ = mc.scan([tile("9", "Pokemon Journey Together Booster Bundle", price=39.99)], BOOK, 20, {})
        message = mc.arrivals_message(arrivals, "Parkville, MD")
        self.assertNotIn("content", message)


class GatherTests(unittest.TestCase):
    def fake_search(self, query, store_id):
        self.assertEqual(store_id, "125")
        return [tile("1", "Pokemon 30th Celebration Elite Trainer Box"), tile("2", "Pokemon Card Sleeves")]

    def test_results_are_deduped_across_queries(self):
        items = mc.gather(CONFIG, search=self.fake_search)
        self.assertEqual(sorted(i["product_id"] for i in items), ["1", "2"])

    def test_run_posts_deals_and_records_state(self):
        posted = []
        summary = mc.run(CONFIG, BOOK, {}, NOW, search=self.fake_search, send=posted.append)
        self.assertEqual(summary["deals"], 1)
        self.assertEqual(summary["seen"]["1"]["pays"], True)
        self.assertTrue(any(p["content"] == "@everyone" for p in posted))

    def test_a_dead_session_stops_cleanly(self):
        def refuses(query, store_id):
            raise mc.McenterUnavailable("no valid Micro Center session")
        with self.assertRaises(mc.McenterUnavailable):
            mc.run(CONFIG, BOOK, {}, NOW, search=refuses, send=lambda m: None)


if __name__ == "__main__":
    unittest.main()
