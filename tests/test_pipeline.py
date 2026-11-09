import json
import unittest
from datetime import date, datetime, timedelta, timezone

from pipeline import calendar_data, classify, common, feeds, ics, market, profit, render, rss

RULES = classify.load_rules()
NEWS = {"id": "n", "name": "News", "default": "news"}
CARDS = {"id": "c", "name": "Cards", "default": "restocks", "cards_only": True}
ASSUMPTIONS = common.load_json(common.CONFIG_DIR / "assumptions.json")
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)


def decide(title, source=NEWS, link="https://example.com/a"):
    return classify.classify(title, link, source, RULES)


class ClassifyTests(unittest.TestCase):
    def test_keeps_sealed_news_with_accented_name(self):
        result = decide("Pokémon TCG 30th Celebration preorders are live")
        self.assertEqual(result["category"], "preorders")
        self.assertTrue(result["hot"] is False or result["hot"] is True)

    def test_rejects_gamestop_even_from_card_sources(self):
        self.assertIsNone(decide("GameStop lists ETB at triple price", CARDS))

    def test_rejects_plush_clothing_and_video_games(self):
        for title in (
            "Pokémon TCG plush bundle on sale",
            "New Pokémon TCG hoodie released",
            "Pokémon TCG Pocket: Dual Fusion Is Now Available",
            "Pokémon Pokopia DLC gets a booster",
        ):
            self.assertIsNone(decide(title), title)

    def test_rejects_general_pokemon_news_without_card_terms(self):
        self.assertIsNone(decide("Pokémon Bank Service is Shutting Down in 2027"))
        self.assertIsNone(decide("New Pokémon anime season announced"))

    def test_rejects_bot_seller_links(self):
        self.assertIsNone(decide("Pokémon TCG Walmart queue guide", link="https://autoqueue.app/drops/walmart"))

    def test_figure_collection_is_not_rejected(self):
        self.assertIsNotNone(decide("Pokémon TCG 30th Celebration Figure Collection restock"))

    def test_store_post_is_a_restock(self):
        result = decide("Delta Reign Elite Trainer Box Live on target", CARDS)
        self.assertEqual(result["category"], "restocks")
        self.assertEqual(result["stores"], ["Target"])
        self.assertTrue(result["hot"])

    def test_closed_drawing_is_not_hot(self):
        self.assertFalse(decide("Walmart Prismatic drawing just closed (ETB $69)", CARDS)["hot"])

    def test_price_in_title_is_a_deal(self):
        self.assertEqual(decide("Pokémon TCG 3 Pack Blister $9.99 at Walmart")["category"], "deals")

    def test_forum_noise_is_dropped(self):
        for title in ("AwesomeAwsm", "Help - Real or fake", "Monthly Informational and Q/A Mega thread!"):
            self.assertIsNone(decide(title, CARDS), title)

    def test_auction_prices_are_not_deals(self):
        self.assertNotEqual(decide("Retiree's $2.6m Pokemon cards sale at auction")["category"], "deals")

    def test_police_stories_and_listicles_are_dropped(self):
        self.assertIsNone(decide("Police: man left daughter outside Target for Pokémon cards"))
        self.assertIsNone(decide("The 10 Most Expensive Delta Reign Pokémon Cards"))

    def test_product_reveal_is_kept_but_card_reveal_is_not(self):
        self.assertEqual(decide("Mega Rayquaza ex Premium Tournament Collection Revealed", CARDS)["category"], "releases")
        self.assertIsNone(decide('Meowth and Persian Revealed from "Aura Seeker!"', CARDS))

    def test_japanese_stock_dropped_but_news_kept(self):
        self.assertIsNone(decide("Japanese Pokémon booster box restock live now"))
        self.assertIsNotNone(decide("Japanese Pokémon TCG set revealed"))


class FeedMergeTests(unittest.TestCase):
    SOURCES = [NEWS, CARDS]

    def entry(self, title, published="2026-11-07T12:00:00Z", source_id="n"):
        return {"title": title, "link": "https://example.com/x", "source": "News", "source_id": source_id, "publisher": "", "published": published}

    def test_same_headline_merges_to_earliest_time(self):
        items = feeds.merge_items([], [
            self.entry("Pokémon TCG restock at Target", "2026-11-07T12:00:00Z"),
            self.entry("Pokemon TCG Restock at Target!", "2026-11-07T10:00:00Z", "c"),
        ], self.SOURCES, RULES, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published"], "2026-11-07T10:00:00Z")

    def test_same_headline_in_a_later_week_is_a_new_story(self):
        items = feeds.merge_items([], [
            self.entry("Walmart Pokémon drawing is open now", "2026-10-28T21:00:00Z", "c"),
            self.entry("Walmart Pokémon drawing is open now", "2026-11-04T21:00:00Z", "c"),
        ], self.SOURCES, RULES, NOW)
        self.assertEqual(len(items), 2)

    def test_old_stories_expire(self):
        items = feeds.merge_items([], [self.entry("Pokémon TCG restock at Target", "2026-09-01T12:00:00Z")], self.SOURCES, RULES, NOW)
        self.assertEqual(items, [])

    def test_stored_items_keep_first_seen(self):
        first = feeds.merge_items([], [self.entry("Pokémon TCG restock at Target")], self.SOURCES, RULES, NOW)
        later = feeds.merge_items(first, [], self.SOURCES, RULES, NOW + timedelta(hours=2))
        self.assertEqual(later[0]["first_seen"], first[0]["first_seen"])

    def test_refused_source_is_recorded_once_and_not_retried(self):
        class Response:
            status_code = 403
            content = b""

        class Session:
            headers = {}
            calls = 0

            def get(self, url, timeout):
                Session.calls += 1
                return Response()

        entries, health = feeds.fetch_sources([{"id": "a", "name": "A", "url": "https://example.com/feed"}], NOW, Session(), pause=0)
        self.assertEqual(entries, [])
        self.assertEqual(Session.calls, 1)
        self.assertEqual(health[0], {"id": "a", "name": "A", "ok": False, "status": 403, "count": 0})

    def test_client_name_is_not_a_browser(self):
        self.assertNotIn("Mozilla", common.USER_AGENT)
        self.assertNotIn("@", common.USER_AGENT)


class ProfitTests(unittest.TestCase):
    def test_elite_trainer_box_matches_hand_calculation(self):
        result = profit.estimate(86.43, 49.99, "etb", ASSUMPTIONS)
        # Total 92.48 at 13.25% = 12.25, plus 0.40 per order, plus 10.50 shipping.
        self.assertEqual(result["fees"], 12.65)
        self.assertEqual(result["basis"], 53.49)
        self.assertEqual(result["profit"], 9.79)

    def test_small_sale_uses_small_per_order_fee(self):
        self.assertAlmostEqual(profit.estimate(9.0, 4.49, "pack", ASSUMPTIONS)["fees"], 1.58, places=2)

    def test_top_rated_discount_skips_per_order_fee(self):
        plain = profit.estimate(100, 50, "etb", ASSUMPTIONS)
        discounted = profit.estimate(100, 50, "etb", {**ASSUMPTIONS, "top_rated": True})
        self.assertAlmostEqual(plain["fees"] - discounted["fees"], (plain["fees"] - 0.40) * 0.1, places=2)

    def test_above_fee_cap(self):
        settings = {**ASSUMPTIONS, "buyer_tax": 0, "fee_cap": 2500, "fee_rate": 0.1235}
        expected = 2500 * 0.1235 + 500 * 0.0235 + 0.40
        self.assertAlmostEqual(profit.estimate(3000, 100, "box", settings)["fees"], expected, places=2)


class MarketTests(unittest.TestCase):
    CONFIG = common.load_json(common.CONFIG_DIR / "products.json")
    GROUP = {"groupId": 24831, "name": "ME06: Delta Reign"}

    def sealed(self, product_id, name, release="2026-11-06T00:00:00", presale=False):
        return {"productId": product_id, "name": name, "url": "https://t/x", "extendedData": [], "presaleInfo": {"isPresale": presale, "releasedOn": release}}

    def rows(self, products, prices):
        return market.product_rows(self.GROUP, products, prices, self.CONFIG, ASSUMPTIONS, date(2026, 11, 8))

    def test_pokemon_center_box_is_not_priced_as_a_regular_one(self):
        rows, _ = self.rows(
            [self.sealed(1, "Delta Reign Pokemon Center Elite Trainer Box (Exclusive)"), self.sealed(2, "Delta Reign Elite Trainer Box")],
            [{"productId": 1, "marketPrice": 200.0}, {"productId": 2, "marketPrice": 80.0}],
        )
        self.assertEqual([row["msrp"] for row in rows], [59.99, 49.99])

    def test_cards_cases_and_displays_are_left_out(self):
        card = {**self.sealed(1, "Mega Charizard ex"), "extendedData": [{"name": "Number", "value": "1/1"}]}
        rows, _ = self.rows(
            [card, self.sealed(2, "Delta Reign Elite Trainer Box Case"), self.sealed(3, "Delta Reign Mini Tin Display")],
            [{"productId": n, "marketPrice": 50.0} for n in (1, 2, 3)],
        )
        self.assertEqual(rows, [])

    def test_presale_listing_is_reported_even_without_a_price(self):
        rows, presale = self.rows([self.sealed(1, "Delta Reign Build & Battle Box", "2026-11-20T00:00:00", True)], [])
        self.assertEqual(rows, [])
        self.assertEqual(presale[0]["release"], "2026-11-20")

    def test_modern_groups_filter(self):
        groups = [
            {"groupId": 24831, "name": "ME06: Delta Reign"},
            {"groupId": 24722, "name": "ME: 30th Celebration"},
            {"groupId": 24451, "name": "ME: Mega Evolution Promo"},
            {"groupId": 24461, "name": "MEE: Mega Evolution Energies"},
            {"groupId": 24938, "name": "McDonald's Promos 2026"},
            {"groupId": 22873, "name": "SV01: Scarlet & Violet Base Set"},
        ]
        self.assertEqual([g["groupId"] for g in market.modern_groups(groups)], [24831, 24722, 22873])

    def listing(self, title, price, shipping="0.00"):
        return {"title": title, "price": {"value": str(price)}, "shippingOptions": [{"shippingCost": {"value": shipping}}]}

    def test_ask_uses_median_of_believable_listings(self):
        listings = [
            self.listing("Pokemon Delta Reign Elite Trainer Box ETB Factory Sealed", 70),
            self.listing("Delta Reign Elite Trainer Box sealed", 72, "3.00"),
            self.listing("Delta Reign ETB Pokemon TCG", 80),
            self.listing("Delta Reign Elite Trainer Box EMPTY", 15),
            self.listing("Delta Reign Pokemon Center Elite Trainer Box", 75),
            self.listing("Delta Reign Elite Trainer Box", 5),
        ]
        self.assertEqual(market.ask_from_listings("Delta Reign Elite Trainer Box", 80.0, listings), (75.0, 3))

    def test_sold_search_link_drops_brackets(self):
        url = market.ebay_sold_url("Delta Reign 3 Pack Blister [Seel]")
        self.assertIn("LH_Sold=1", url)
        self.assertNotIn("%5B", url)

    def test_no_believable_listings(self):
        self.assertEqual(market.ask_from_listings("Delta Reign Elite Trainer Box", 80.0, []), (None, 0))


class CalendarTests(unittest.TestCase):
    WINDOW = {
        "id": "w", "store": "Walmart", "title": "Drawing entry window", "days": [2], "start": "17:00", "end": "18:00",
        "status": "reported", "note": "n", "action": "a", "link": "https://w", "calendar_days": [2], "remind_days": [2], "remind_minutes": 45,
    }

    def test_window_uses_eastern_time_across_daylight_change(self):
        before = calendar_data.occurrence(self.WINDOW, date(2026, 10, 28))
        after = calendar_data.occurrence(self.WINDOW, date(2026, 11, 4))
        self.assertEqual(before["start"], "2026-10-28T21:00:00Z")
        self.assertEqual(after["start"], "2026-11-04T22:00:00Z")

    def test_schedule_labels(self):
        self.assertEqual(calendar_data.schedule_label([0, 1, 2, 3, 4]), "Every weekday")
        self.assertEqual(calendar_data.schedule_label([2]), "Every Wednesday")
        self.assertEqual(calendar_data.schedule_label([1, 3]), "Tue, Thu")

    def test_finished_window_is_dropped(self):
        evening = datetime(2026, 11, 11, 23, 30, tzinfo=timezone.utc)
        found = calendar_data.upcoming_windows([self.WINDOW], evening, days=1)
        self.assertEqual(found, [])

    def test_presale_events_skip_curated_products(self):
        curated = [{"id": "x", "date": "2026-12-04", "covers": ["30th Celebration Binder Collection"]}]
        presale = [
            {"id": 1, "name": "30th Celebration Binder Collection", "set": "s", "release": "2026-12-04"},
            {"id": 2, "name": "Delta Reign Build & Battle Box", "set": "s", "release": "2026-11-20"},
        ]
        events = calendar_data.presale_events(presale, curated, date(2026, 11, 8))
        self.assertEqual([e["date"] for e in events], ["2026-11-20"])

    def test_past_ranges_stay_until_they_end(self):
        curated = [{"id": "x", "title": "T", "date": "2026-11-01", "end": "2026-11-20", "status": "reported"}]
        self.assertEqual(len(calendar_data.upcoming_events(curated, [], NOW)), 1)


class FileFormatTests(unittest.TestCase):
    EVENT = {"id": "e", "title": "Box, with; punctuation", "date": "2026-12-04", "status": "reported", "note": "A note", "sources": [{"label": "L", "url": "https://u"}]}

    def test_calendar_lines_are_folded_and_escaped(self):
        text = ics.build_calendar([self.EVENT], [CalendarTests.WINDOW], NOW)
        self.assertIn("Box\\, with\\; punctuation", text.replace("\r\n ", ""))
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split("\r\n")))
        self.assertIn("DTEND;VALUE=DATE:20261205", text)
        self.assertIn("RRULE:FREQ=WEEKLY;BYDAY=WE", text)

    def test_rss_escapes_titles(self):
        item = {"title": "A & B <script>", "link": "https://x/?a=1&b=2", "guid": "g", "published": "2026-11-07T12:00:00Z", "category": "news"}
        text = rss.render_feed("T", "D", "feed.xml", [item], NOW)
        self.assertIn("A &amp; B &lt;script&gt;", text)
        self.assertIn("a=1&amp;b=2", text)
        self.assertIn("Sat, 07 Nov 2026 12:00:00 GMT", text)

    def test_page_data_cannot_close_the_script_tag(self):
        self.assertNotIn("</script>", render.safe_json({"title": "</script><script>alert(1)</script>"}))


class ConfigTests(unittest.TestCase):
    def test_events_are_valid(self):
        data = common.load_json(common.DATA_DIR / "events.json")
        for event in data["events"]:
            date.fromisoformat(event["date"])
            self.assertIn(event["status"], {"official", "reported", "estimate"})
            self.assertTrue(event["sources"], event["id"])

    def test_windows_are_valid(self):
        for window in common.load_json(common.DATA_DIR / "windows.json")["windows"]:
            self.assertLess(window["start"], window["end"])
            self.assertTrue(set(window["calendar_days"]) <= set(window["days"]), window["id"])
            self.assertTrue(window["sources"], window["id"])

    def test_gamestop_is_only_searched_for_free_promos(self):
        sources = common.load_json(common.CONFIG_DIR / "sources.json")["sources"]
        self.assertEqual([s["id"] for s in sources if "gamestop" in json.dumps(s).lower()], ["gn-promo-stores"])


if __name__ == "__main__":
    unittest.main()
