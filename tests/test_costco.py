import unittest
from datetime import datetime, timedelta, timezone

from pipeline import classify, common, costco_stock, discord_post
from pipeline.common import iso

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)

PAGE = """
<p>Sheet date: <b>Nov 7</b> · Counts last changed</p>
<h2>Massachusetts totals</h2><p>30th Celebration Elite Trainer Box + Booster Bundle · item 2007496</p>
<table><tbody id="wt-rows"><tr><th scope="row" class="w-store"><span class="nm">Avon MA</span> <span class="ct">#303</span><span class="w-addr">120 Stockwell Dr<span class="w-ac">, Avon</span> 02322</span></th><td class="w-city">Avon</td><td class="num" data-l="On hand">1,440</td><td class="num" data-l="On order">0</td><td class="num" data-l="In transit">240</td></tr></tbody></table>
<table><tbody><tr><th scope="row" class="w-store"><span class="nm">Danvers</span> <span class="ct">#301</span></th><td>Danvers</td><td colspan="3">Not on the latest allocation sheet</td></tr></tbody></table>
"""


class FakeClient:
    def __init__(self):
        self.sent = []
        self.boards = {}

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.boards[channel] = payload
        return message_id or "id"


def warehouse(number, name, hand=0, order=0, transit=0):
    return {"number": number, "name": name, "hand": hand, "order": order, "transit": transit}


def sheet(rows, date="Nov 8"):
    return {"sheet_date": date, "product": "Bundle", "item": "2007496", "states": {"NJ": rows}}


class ParseTests(unittest.TestCase):
    def test_reads_counts_and_sheet_details(self):
        page = costco_stock.parse_page(PAGE)
        self.assertEqual(page["sheet_date"], "Nov 7")
        self.assertEqual(page["item"], "2007496")
        self.assertEqual(page["product"], "30th Celebration Elite Trainer Box + Booster Bundle")
        self.assertEqual(page["warehouses"], [warehouse("303", "Avon MA", hand=1440, transit=240)])

    def test_status_prefers_what_is_closest_to_the_shelf(self):
        self.assertEqual(costco_stock.status_of(warehouse("1", "A", hand=1, transit=5)), "hand")
        self.assertEqual(costco_stock.status_of(warehouse("1", "A", order=5, transit=5)), "transit")
        self.assertEqual(costco_stock.status_of(warehouse("1", "A", order=5)), "order")
        self.assertEqual(costco_stock.status_of(warehouse("1", "A")), "none")


class FetchTests(unittest.TestCase):
    class Response:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text

    def session(self, responses):
        class Session:
            headers = {}
            calls = 0

            def get(inner, url, timeout):
                Session.calls += 1
                return responses.pop(0)

        return Session()

    def test_refusal_ends_the_pass_without_retrying(self):
        session = self.session([self.Response(403)])
        self.assertIsNone(costco_stock.fetch_states(session, pause=0))
        self.assertEqual(type(session).calls, 1)

    def test_identifies_itself_honestly(self):
        session = self.session([self.Response(403)])
        costco_stock.fetch_states(session, pause=0)
        self.assertNotIn("Mozilla", session.headers["User-Agent"])

    def test_checks_only_a_few_times_a_day(self):
        watched = sorted(common.load_json(common.CONFIG_DIR / "costco.json")["states"])
        state = {"costco_checked_at": iso(NOW - timedelta(hours=1)), "costco_watched": watched}
        self.assertIsNone(costco_stock.refresh_if_due(NOW, state, self.session([])))

    def test_changing_the_states_checks_right_away(self):
        state = {"costco_checked_at": iso(NOW - timedelta(minutes=5)), "costco_watched": ["ohio"]}
        session = self.session([self.Response(403)])
        costco_stock.refresh_if_due(NOW, state, session, pause=0)
        self.assertEqual(type(session).calls, 1)

    def test_a_state_with_no_stock_still_counts_as_a_good_read(self):
        empty = "<p>Sheet date: Nov 7</p><p>Maryland totals Bundle · item 1</p>"
        pages = len(common.load_json(common.CONFIG_DIR / "costco.json")["states"])
        data = costco_stock.fetch_states(self.session([self.Response(200, empty) for _ in range(pages)]), pause=0)
        self.assertEqual(data["states"], {})
        self.assertEqual(data["sheet_date"], "Nov 7")
        self.assertEqual(data["focus"], "MD")

    def test_a_failed_check_still_waits_before_trying_again(self):
        state = {}
        costco_stock.refresh_if_due(NOW, state, self.session([self.Response(429)]), pause=0)
        self.assertEqual(state["costco_checked_at"], iso(NOW))


class ApplyTests(unittest.TestCase):
    def test_first_check_only_records(self):
        client, state = FakeClient(), {}
        discord_post.apply_costco(client, state, sheet([warehouse("1", "Princeton NJ", hand=480)]), set())
        self.assertEqual(client.sent, [])
        self.assertIn("costco", client.boards)
        self.assertEqual(state["costco_status"], {"1": "hand"})

    def test_new_stock_is_posted_and_pings_once_per_sheet(self):
        client, keys = FakeClient(), set()
        state = {"costco_status": {"1": "transit", "2": "none"}}
        rows = [warehouse("1", "Princeton NJ", hand=480), warehouse("2", "Mt Laurel NJ", transit=240)]
        discord_post.apply_costco(client, state, sheet(rows), keys)
        self.assertEqual([channel for channel, _ in client.sent], ["costco", "drop-pings"])
        text = client.sent[0][1]["embeds"][0]["description"]
        self.assertIn("Princeton NJ (480)", text)
        self.assertIn("Mt Laurel NJ (240)", text)

        state["costco_status"]["1"] = "none"
        discord_post.apply_costco(client, state, sheet(rows), keys)
        self.assertEqual([channel for channel, _ in client.sent].count("drop-pings"), 1)

    def test_no_change_means_no_post(self):
        client, state = FakeClient(), {"costco_status": {"1": "hand"}}
        discord_post.apply_costco(client, state, sheet([warehouse("1", "Princeton NJ", hand=480)]), set())
        self.assertEqual(client.sent, [])

    def test_stock_going_backward_is_not_news(self):
        client, state = FakeClient(), {"costco_status": {"1": "hand"}}
        discord_post.apply_costco(client, state, sheet([warehouse("1", "Princeton NJ", transit=240)]), set())
        self.assertEqual(client.sent, [])

    def test_board_fits_discord_limits_with_a_big_sheet(self):
        rows = [warehouse(str(n), f"Warehouse Number {n} NJ", hand=480, transit=240) for n in range(60)]
        data = {"sheet_date": "Nov 8", "product": "Bundle", "item": "1", "states": {f"S{n}": rows for n in range(23)}}
        embed = discord_post.costco_board(data)["embeds"][0]
        self.assertLessEqual(len(embed["fields"]), 25)
        self.assertTrue(all(len(field["value"]) <= 1024 for field in embed["fields"]))
        total = len(embed["title"]) + len(embed["description"]) + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
        self.assertLessEqual(total, 6000)

    def test_home_state_always_comes_first(self):
        data = {
            "sheet_date": "Nov 8", "product": "Bundle", "item": "1", "focus": "MD", "area": "Maryland and nearby",
            "states": {"PA": [warehouse("1", "Oaks PA", hand=960)], "MD": [warehouse("2", "Columbia MD", transit=240)]},
        }
        embed = discord_post.costco_board(data)["embeds"][0]
        self.assertTrue(embed["fields"][0]["name"].startswith("MD:"))
        self.assertIn("Maryland and nearby", embed["title"])

    def test_home_state_shows_even_with_no_stock(self):
        data = {
            "sheet_date": "Nov 8", "product": "Bundle", "item": "1", "focus": "MD", "area": "Maryland and nearby",
            "states": {"PA": [warehouse("1", "Oaks PA", hand=960)]},
        }
        fields = discord_post.costco_board(data)["embeds"][0]["fields"]
        self.assertEqual(fields[0]["name"], "MD: nothing on this sheet yet")
        self.assertTrue(fields[1]["name"].startswith("PA:"))

    def test_state_with_nothing_is_left_off_the_board(self):
        data = {"sheet_date": "Nov 8", "product": "Bundle", "item": "1", "states": {"MA": [warehouse("1", "Avon MA")]}}
        self.assertEqual(discord_post.costco_board(data)["embeds"][0]["fields"][0]["name"], "Nothing yet")


class RetiredMessageTests(unittest.TestCase):
    def test_old_list_is_replaced_with_a_note_once(self):
        client = FakeClient()
        state = {"initialized": True, "retired_messages": [{"channel": "costco", "id": "9"}]}
        discord_post.run(NOW, client, [], [], [], [], [], state)
        self.assertEqual(client.boards["costco"]["content"], discord_post.RETIRED_NOTE)
        self.assertEqual(client.boards["costco"]["embeds"], [])
        self.assertNotIn("retired_messages", state)


class SourceFilterTests(unittest.TestCase):
    RULES = classify.load_rules()
    SOURCE = {"id": "x", "name": "X", "default": "restocks", "cards_only": True, "only_terms": ["costco", "sam's club"]}

    def test_only_club_pages_get_through(self):
        decision = classify.classify("Sam's Club 151 Mini Tins", "https://x", self.SOURCE, self.RULES)
        self.assertEqual(decision["stores"], ["Sam's Club"])
        self.assertIsNone(classify.classify("Target restock times this week", "https://x", self.SOURCE, self.RULES))


if __name__ == "__main__":
    unittest.main()
