import unittest
from datetime import date

from pipeline import accessories

STOP = {"pokemon", "the", "of", "and", "new", "official", "inch", "toy", "plushie", "set"}
SHIP = {"keyboard": "box", "plush": "pack", "default": "pack"}


def listing(title, price, ship=None):
    out = {"title": title, "price": {"value": f"{price}"}}
    if ship is not None:
        out["shippingOptions"] = [{"shippingCost": {"value": f"{ship}"}}]
    return out


class TokenTests(unittest.TestCase):
    def test_drops_stopwords_numbers_and_apostrophes(self):
        self.assertEqual(accessories.tokens("Pokémon Snorlax Plush 24 inch Official", STOP), ["snorlax", "plush"])

    def test_possessive_folds_to_one_word(self):
        self.assertEqual(accessories.tokens("Trainer's Choice Keyboard", STOP), ["trainers", "choice", "keyboard"])

    def test_signature_is_order_independent(self):
        self.assertEqual(accessories.signature(["plush", "snorlax"]), accessories.signature(["snorlax", "plush"]))

    def test_ship_follows_the_first_known_keyword(self):
        self.assertEqual(accessories.ship_for(["snorlax", "plush"], SHIP, "pack"), "pack")
        self.assertEqual(accessories.ship_for(["mechanical", "keyboard"], SHIP, "pack"), "box")
        self.assertEqual(accessories.ship_for(["mystery", "widget"], SHIP, "bundle"), "bundle")


class PriceTests(unittest.TestCase):
    def test_total_adds_shipping(self):
        self.assertEqual(accessories.listing_total(listing("x", 40, 5)), 45.0)

    def test_total_without_shipping(self):
        self.assertEqual(accessories.listing_total(listing("x", 40)), 40.0)

    def test_believable_price_is_median_of_cheapest_five(self):
        price, count = accessories.believable_price([200, 10, 40, 45, 50, 55, 60], min_price=12)
        # The 10 is below min_price; cheapest five believable are 40,45,50,55,60 -> median 50.
        self.assertEqual(price, 50.0)
        self.assertEqual(count, 6)

    def test_believable_price_empty(self):
        self.assertEqual(accessories.believable_price([5, 8], min_price=12), (None, 0))


class GroupTests(unittest.TestCase):
    def test_clusters_same_item_and_skips_single_word(self):
        rows = [
            listing("Pokemon Snorlax Plush 18 inch", 45),
            listing("Pokemon Snorlax Plush Official", 49.99),
            listing("Pokemon Snorlax Plush", 52),
            listing("Pokemon Plush", 30),  # one significant word -> skipped
        ]
        groups = accessories.group_listings(rows, STOP, [], SHIP, "pack")
        self.assertIn("plush|snorlax", groups)
        self.assertEqual(len(groups["plush|snorlax"]["totals"]), 3)
        self.assertNotIn("plush", groups)

    def test_exclude_drops_bad_listings(self):
        import re
        rows = [listing("Pokemon Snorlax Plush japanese", 45), listing("Pokemon Snorlax Plush", 50)]
        groups = accessories.group_listings(rows, STOP, [re.compile("japanese")], SHIP, "pack")
        self.assertEqual(len(groups["plush|snorlax"]["totals"]), 1)

    def test_items_respect_min_listings(self):
        rows = [listing("Pokemon Snorlax Plush", 50), listing("Pokemon Snorlax Plush Official", 52)]
        groups = accessories.group_listings(rows, STOP, [], SHIP, "pack")
        self.assertEqual(accessories.items_from_groups(groups, min_listings=3, min_price=12, today=date(2026, 11, 12)), [])


class HistoryTests(unittest.TestCase):
    def item(self, price, day, count=5):
        return {"key": "plush|snorlax", "tokens": ["snorlax", "plush"], "title": "Snorlax Plush",
                "ship": "pack", "price": price, "count": count, "date": day}

    def test_new_item_takes_its_price(self):
        merged = accessories.merge_history([], [self.item(50, "2026-11-12")], date(2026, 11, 12), 30)
        self.assertEqual(merged[0]["market"], 50.0)
        self.assertEqual(merged[0]["last_seen"], "2026-11-12")

    def test_market_is_median_over_days(self):
        day1 = accessories.merge_history([], [self.item(50, "2026-11-12")], date(2026, 11, 12), 30)
        day2 = accessories.merge_history(day1, [self.item(60, "2026-11-13")], date(2026, 11, 13), 30)
        self.assertEqual(day2[0]["market"], 55.0)
        self.assertEqual(len(day2[0]["history"]), 2)

    def test_same_day_rerun_replaces_not_appends(self):
        day1 = accessories.merge_history([], [self.item(50, "2026-11-12")], date(2026, 11, 12), 30)
        again = accessories.merge_history(day1, [self.item(70, "2026-11-12")], date(2026, 11, 12), 30)
        self.assertEqual(len(again[0]["history"]), 1)
        self.assertEqual(again[0]["market"], 70.0)

    def test_stale_items_are_dropped(self):
        day1 = accessories.merge_history([], [self.item(50, "2026-11-12")], date(2026, 11, 12), 30)
        later = accessories.merge_history(day1, [], date(2026, 12, 20), 30)
        self.assertEqual(later, [])


if __name__ == "__main__":
    unittest.main()
