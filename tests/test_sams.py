import unittest
from datetime import datetime, timedelta, timezone

from pipeline import discord_post, sams_stock
from pipeline.common import iso

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
URL = "https://pokerestockapp.com/sams-club/pokemon-151-mini-tins/"

PAGE = """
<p class="pfresh"><span class="status">Latest club sheet</span> Sheet date: November 8, 2026 · Last checked <time>Nov 10</time></p>
<p>Reported order counts for the Pokémon TCG 151 Mini Tins (Sam&#x2019;s Club item #990416407) at 395 US Sam's Club locations.</p>
<details class="sc-state" id="st-maine"><summary><h3 class="acc-t">Maine</h3></summary><ul class="sc-list" role="list">
<li class="sc-row"><div class="sc-main"><span class="nm">Bangor</span> <span class="ct">Club #6333</span></div><div class="sc-qs"><span class="sc-q k-on_order_qty hot"><b>160</b> on order</span></div></li>
</ul></details>
<details class="sc-state" id="st-maryland"><summary><h3 class="acc-t">Maryland</h3> <span class="dim">2 listings</span></summary><ul class="sc-list" role="list">
<li class="sc-row"><div class="sc-main"><span class="nm">Annapolis</span> <span class="ct">Club #6357</span><span class="w-addr">2100 Generals Hwy, Annapolis, MD 21401</span></div><div class="sc-qs"><span class="sc-q k-on_order_qty hot"><b>320</b> on order</span></div></li>
<li class="sc-row"><div class="sc-main"><span class="nm">Glen Burnie</span> <span class="ct">City-area listing · club not identified</span></div><div class="sc-qs"><span class="sc-q k-on_order_qty"><b>1,120</b> on order</span></div></li>
</ul></details>
"""


class FakeClient:
    def __init__(self):
        self.sent = []
        self.boards = {}

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.boards[(channel, len(self.boards))] = payload
        return message_id or f"id{len(self.boards)}"


class Response:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


def session(responses):
    class Session:
        headers = {}
        calls = 0

        def get(inner, url, timeout):
            Session.calls += 1
            return responses.pop(0)

    return Session()


def club(number, name, order=0, transit=0, hand=0):
    return {"number": number, "name": name, "hand": hand, "order": order, "transit": transit}


def item(clubs, date="November 8, 2026", number="990416407"):
    return {"sheet_date": date, "product": "Pokémon TCG 151 Mini Tins", "item": number, "url": URL, "states": {"MD": clubs}, "focus": "MD", "area": "Maryland and nearby"}


class ParseTests(unittest.TestCase):
    def test_reads_clubs_counts_and_sheet_details(self):
        page = sams_stock.parse_item_page(PAGE)
        self.assertEqual(page["sheet_date"], "November 8, 2026")
        self.assertEqual(page["item"], "990416407")
        self.assertEqual(page["product"], "Pokémon TCG 151 Mini Tins")
        self.assertEqual(page["states"]["maryland"][0], club("6357", "Annapolis", order=320))

    def test_a_city_area_listing_is_kept_without_a_club_number(self):
        page = sams_stock.parse_item_page(PAGE)
        self.assertEqual(page["states"]["maryland"][1], club("", "Glen Burnie area", order=1120))


class FetchTests(unittest.TestCase):
    SITEMAP = f"<urlset><url><loc>{URL}</loc></url><url><loc>https://pokerestockapp.com/costco/</loc></url></urlset>"

    def test_keeps_only_the_watched_states(self):
        items = sams_stock.fetch_items(session([Response(200, self.SITEMAP), Response(200, PAGE)]), pause=0)
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]["states"]), {"MD"})
        self.assertEqual(items[0]["url"], URL)

    def test_a_refusal_ends_the_pass(self):
        fake = session([Response(403)])
        self.assertIsNone(sams_stock.fetch_items(fake, pause=0))
        self.assertEqual(type(fake).calls, 1)

    def test_looks_at_most_every_half_hour(self):
        state = {"sams_checked_at": iso(NOW - timedelta(minutes=10))}
        self.assertIsNone(sams_stock.refresh_if_due(NOW, state, session([])))

    def test_a_failed_check_still_waits(self):
        state = {}
        sams_stock.refresh_if_due(NOW, state, session([Response(429)]), pause=0)
        self.assertEqual(state["sams_checked_at"], iso(NOW))


class ApplyTests(unittest.TestCase):
    def test_first_check_only_records(self):
        client, state = FakeClient(), {}
        discord_post.apply_sams(client, state, [item([club("6357", "Annapolis", order=320)])], set())
        self.assertEqual(client.sent, [])
        self.assertEqual(len(client.boards), 1)
        self.assertEqual(state["sams_status"], {"990416407": {"6357": "order"}})

    def test_a_club_new_to_the_sheet_is_posted_and_pings_once(self):
        client, keys = FakeClient(), set()
        state = {"sams_status": {"990416407": {"6357": "order"}}}
        sheet = item([club("6357", "Annapolis", order=320), club("6653", "Gaithersburg", order=320)])
        discord_post.apply_sams(client, state, [sheet], keys)
        self.assertEqual([channel for channel, _ in client.sent], ["sams-club", "drop-pings"])
        self.assertIn("Gaithersburg MD (320)", client.sent[0][1]["embeds"][0]["description"])
        self.assertNotIn("Annapolis", client.sent[0][1]["embeds"][0]["description"])
        state["sams_status"]["990416407"]["6653"] = "none"
        discord_post.apply_sams(client, state, [sheet], keys)
        self.assertEqual([channel for channel, _ in client.sent].count("drop-pings"), 1)

    def test_stock_moving_closer_to_the_shelf_is_news(self):
        client, state = FakeClient(), {"sams_status": {"990416407": {"6357": "order"}}}
        discord_post.apply_sams(client, state, [item([club("6357", "Annapolis", transit=320)])], set())
        self.assertIn("On the truck", client.sent[0][1]["embeds"][0]["description"])

    def test_counts_are_never_saved(self):
        state = {}
        discord_post.apply_sams(FakeClient(), state, [item([club("6357", "Annapolis", order=320)])], set())
        self.assertNotIn("320", str(state["sams_status"]))

    def test_nothing_nearby_still_says_so(self):
        embed = discord_post.sams_shipments_board([{**item([]), "states": {}}])["embeds"][0]
        self.assertIn("MD: nothing on this sheet yet", embed["fields"][0]["name"])

    def test_board_fits_discord_limits(self):
        clubs = [club(str(n), f"Some Long Club Name Number {n}", order=320) for n in range(40)]
        items = [{**item(clubs, number=str(n)), "states": {s: clubs for s in ("MD", "VA", "PA", "DE", "NJ", "DC")}} for n in range(5)]
        board = discord_post.sams_shipments_board(items)
        self.assertLessEqual(len(board["embeds"]), discord_post.SAMS_ITEMS_SHOWN)
        total = 0
        for embed in board["embeds"]:
            self.assertLessEqual(len(embed["fields"]), 25)
            self.assertTrue(all(len(f["value"]) <= 1024 for f in embed["fields"]))
            total += len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
            total += sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
        self.assertLessEqual(total, 6000)

    def test_two_boards_can_share_a_channel(self):
        client, state = FakeClient(), {}
        discord_post.update_board(client, state, "sams-club", {"content": "a"})
        discord_post.update_board(client, state, "sams-club", {"content": "b"}, key="sams-club-shipments")
        self.assertEqual(set(state["boards"]), {"sams-club", "sams-club-shipments"})


if __name__ == "__main__":
    unittest.main()
