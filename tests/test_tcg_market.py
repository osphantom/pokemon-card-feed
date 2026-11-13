import re
import unittest
from datetime import date

from pipeline import tcg_market

SEALED = re.compile("booster box|booster pack|starter deck|\\bdisplay\\b|\\bcollection\\b")
EXCLUDE = re.compile("\\bcase\\b|playmat|sleeve|lot of")
TODAY = date(2026, 11, 13)


def product(pid, name, single=False, presale=False, released=None):
    p = {"productId": pid, "name": name, "url": "https://x"}
    if single:
        p["extendedData"] = [{"name": "Number", "value": "001"}]
    info = {}
    if presale:
        info["isPresale"] = True
    if released:
        info["releasedOn"] = released + "T00:00:00"
    if info:
        p["presaleInfo"] = info
    return p


class SealedTests(unittest.TestCase):
    def rows(self, products, prices):
        return tcg_market.sealed_rows(products, prices, "Some Set", "One Piece", SEALED, EXCLUDE,
                                      min_market=8, current_days=160, today=TODAY)

    def test_keeps_sealed_with_a_price(self):
        rows = self.rows([product(1, "The Dominance of God Booster Box")], {1: {"marketPrice": 228.55}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["game"], "One Piece")
        self.assertEqual(rows[0]["market"], 228.55)

    def test_drops_singles(self):
        self.assertEqual(self.rows([product(1, "Monkey D. Luffy Leader", single=True)], {1: {"marketPrice": 40}}), [])

    def test_drops_excluded_and_accessories(self):
        products = [product(1, "The Dominance of God Booster Box Case"), product(2, "Luffy Playmat")]
        self.assertEqual(self.rows(products, {1: {"marketPrice": 3000}, 2: {"marketPrice": 25}}), [])

    def test_drops_no_or_cheap_price(self):
        products = [product(1, "Sleeved Booster Pack"), product(2, "Starter Deck")]
        self.assertEqual(self.rows(products, {1: {}, 2: {"marketPrice": 5}}), [])

    def test_presale_is_current(self):
        rows = self.rows([product(1, "New Booster Box", presale=True, released="2027-01-01")], {1: {"marketPrice": 90}})
        self.assertTrue(rows[0]["presale"])
        self.assertTrue(rows[0]["current"])

    def test_old_release_is_not_current(self):
        rows = self.rows([product(1, "Old Booster Box", released="2024-01-01")], {1: {"marketPrice": 90}})
        self.assertFalse(rows[0]["current"])


class SingleTests(unittest.TestCase):
    def test_single_detection(self):
        self.assertTrue(tcg_market.is_single(product(1, "x", single=True)))
        self.assertFalse(tcg_market.is_single(product(1, "x")))


def listing(title, price, ship=None):
    out = {"title": title, "price": {"value": str(price)}}
    if ship is not None:
        out["shippingOptions"] = [{"shippingCost": {"value": str(ship)}}]
    return out


class BelievableAskTests(unittest.TestCase):
    def test_median_of_cheapest_believable_box_listings(self):
        listings = [
            listing("One Piece The Dominance of God Booster Box Sealed", 120),
            listing("One Piece Dominance of God Booster Box New", 110),
            listing("One Piece Dominance of God Booster Box", 130),
            listing("One Piece Dominance of God single booster pack", 15),   # not a box
            listing("One Piece Some Other Set Booster Box", 50),             # wrong set
        ]
        ask, count = tcg_market.believable_ask("The Dominance of God Booster Box", listings, min_price=8)
        self.assertEqual(count, 3)
        self.assertEqual(ask, 120.0)

    def test_too_few_listings_is_unconfirmed(self):
        listings = [listing("Dominance of God Booster Box", 120)]
        self.assertEqual(tcg_market.believable_ask("The Dominance of God Booster Box", listings, 8), (None, 1))

    def test_cases_and_lots_never_price_a_single_box(self):
        listings = [
            listing("One Piece Dominance of God Booster Box Case", 1600),
            listing("One Piece Dominance of God Booster Box lot of 3", 350),
            listing("One Piece Dominance of God Booster Box x6", 1500),
            listing("One Piece Dominance of God Booster Box", 120),  # the only real single box
        ]
        self.assertEqual(tcg_market.believable_ask("The Dominance of God Booster Box", listings, 8), (None, 1))

    def test_no_keys_marks_everything_unverified(self):
        rows = [{"market": 424.0, "name": "Star Trek - Collector Booster Display"}]
        tcg_market.ground_with_ebay(rows, None, {"ebay_lookups": 200, "min_market": 8, "min_ebay_count": 3})
        self.assertFalse(rows[0]["verified"])
        self.assertEqual(rows[0]["catalog"], 424.0)


class CapTests(unittest.TestCase):
    ASSUMPTIONS = {"fee_rate": 0.1325, "fee_cap": 7500, "over_rate": 0.0235, "per_order_small": 0.30,
                   "per_order_large": 0.40, "per_order_cutoff": 10, "top_rated": False, "buyer_tax": 0.07,
                   "your_tax": 0.07, "retail_discount": 0, "sell_pct": 1, "supplies": 1, "shipping": {"box": 12}}

    def row(self, market, ask, count):
        return {"market": market, "ebay_ask": ask, "ebay_count": count, "msrp": 115, "ship": "box", "profit": 999, "roi": 9}

    def test_inflated_catalog_is_capped_to_ebay(self):
        from pipeline import market
        rows = [self.row(424.0, 120.0, 10)]
        market.cap_by_ebay(rows, self.ASSUMPTIONS)
        self.assertEqual(rows[0]["market"], 120.0)
        self.assertLess(rows[0]["profit"], 20)  # recomputed, no longer a fake winner

    def test_reasonable_catalog_is_left_alone(self):
        from pipeline import market
        rows = [self.row(100.0, 110.0, 5)]
        market.cap_by_ebay(rows, self.ASSUMPTIONS)
        self.assertEqual(rows[0]["market"], 100.0)

    def test_thin_ebay_data_does_not_cap(self):
        from pipeline import market
        rows = [self.row(300.0, 120.0, 2)]
        market.cap_by_ebay(rows, self.ASSUMPTIONS)
        self.assertEqual(rows[0]["market"], 300.0)


if __name__ == "__main__":
    unittest.main()
