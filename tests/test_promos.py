import re
import unittest
from datetime import date, datetime, timedelta, timezone

from pipeline import classify, common, discord_api, discord_post, promos
from pipeline.common import iso

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 9)
COSTS = common.load_json(common.CONFIG_DIR / "assumptions.json")
NEWS = {"id": "n", "name": "News", "default": "news", "google_news": True}
discord_api.time.sleep = lambda seconds: None
promos.time.sleep = lambda seconds: None


class FakeClient:
    def __init__(self):
        self.sent = []
        self.boards = {}

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.boards[channel] = payload
        return message_id or "id"


def promo(identifier="a", market=None, start="2026-11-06", card="Kyogre promo"):
    base = {
        "id": identifier,
        "card": card,
        "stores": ["Barnes & Noble"],
        "how": "Spend $15.",
        "start": start,
        "end": "",
        "until": "While supplies last",
        "status": "official",
        "group": 1,
        "match": "^Kyogre",
        "sources": [{"label": "Pokémon.com", "url": "https://x"}],
    }
    return promos.priced(base, market, COSTS, TODAY)


class ClassifyTests(unittest.TestCase):
    RULES = classify.load_rules()

    def decide(self, title, source=NEWS):
        return classify.classify(title, "https://x", source, self.RULES)

    def test_gamestop_promo_gets_through(self):
        result = self.decide("GameStop giving away free Pokémon TCG promo card with purchase")
        self.assertEqual(result["category"], "promos")
        self.assertIn("GameStop", result["stores"])

    def test_gamestop_without_a_promo_stays_out(self):
        self.assertIsNone(self.decide("GameStop lists Pokémon TCG Elite Trainer Box at triple price"))

    def test_barnes_and_noble_promo_is_tagged(self):
        result = self.decide("Barnes & Noble has a Kyogre Pokémon TCG promo card this weekend")
        self.assertEqual(result["category"], "promos")
        self.assertIn("Barnes & Noble", result["stores"])

    def test_promo_codes_are_not_promos(self):
        result = self.decide("Pokémon TCG booster box deal with promo code SAVE10")
        self.assertNotEqual(result["category"], "promos")

    def test_other_countries_stay_out(self):
        self.assertIsNone(self.decide("Pokémon TCG promo cards at EB Games Australia"))

    def test_promos_go_to_their_channel(self):
        item = {"category": "promos", "stores": ["GameStop"]}
        self.assertEqual(discord_post.channels_for(item), ["free-promos"])


class ValueTests(unittest.TestCase):
    def test_keep_is_less_than_the_sale_price(self):
        self.assertLess(promos.keep_after_selling(23, COSTS), 23)

    def test_expensive_cards_ship_with_tracking(self):
        just_under = promos.keep_after_selling(19.99, COSTS)
        just_over = promos.keep_after_selling(20, COSTS)
        self.assertLess(just_over, just_under)

    def test_cheap_promo_is_not_worth_showing(self):
        self.assertFalse(promos.worth_showing(promo(market=1.5)))

    def test_valuable_promo_is_shown(self):
        self.assertTrue(promos.worth_showing(promo(market=23)))

    def test_promo_with_no_price_yet_is_shown(self):
        self.assertTrue(promos.worth_showing(promo(market=None)))


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body

    def json(self):
        return {"results": self.body}


class Session:
    def __init__(self, responses):
        self.headers = {}
        self.responses = responses
        self.calls = 0

    def get(self, url, timeout):
        self.calls += 1
        return self.responses.pop(0)


class FetchTests(unittest.TestCase):
    PRODUCTS = [{"productId": 1, "name": "Kyogre - 200"}, {"productId": 2, "name": "Kyogre - 200 [Staff]"}]
    PRICES = [{"productId": 1, "marketPrice": 9.0}, {"productId": 2, "marketPrice": 40.0}]

    def fetch(self, responses):
        session = Session(responses)
        return promos.fetch_markets([promo()], session, pause=0), session

    def test_uses_the_store_version_not_the_staff_stamp(self):
        found, _ = self.fetch([Response(200, self.PRODUCTS), Response(200, self.PRICES)])
        self.assertEqual(found, {"a": 9.0})

    def test_unlisted_card_has_no_price(self):
        found, _ = self.fetch([Response(200, [{"productId": 3, "name": "Pikachu"}]), Response(200, [])])
        self.assertEqual(found, {})

    def test_a_refusal_stops_the_check(self):
        found, session = self.fetch([Response(403), Response(403)])
        self.assertEqual(found, {})
        self.assertEqual(session.calls, 2)

    def test_identifies_itself_honestly(self):
        _, session = self.fetch([Response(403), Response(403)])
        self.assertNotIn("Mozilla", session.headers["User-Agent"])

    def test_prices_are_checked_only_a_few_times_a_day(self):
        state = {"promos_checked_at": iso(NOW - timedelta(hours=1))}
        session = Session([])
        promos.refresh_if_due(NOW, state, session, pause=0)
        self.assertEqual(session.calls, 0)


class BoardTests(unittest.TestCase):
    def test_best_value_comes_first(self):
        board = discord_post.promos_board([promo("a", 8, card="Small"), promo("b", 40, card="Big"), promo("c", None, card="Unknown")])
        names = [field["name"] for field in board["embeds"][0]["fields"]]
        self.assertTrue(names[0].startswith("Big"))
        self.assertTrue(names[-1].startswith("Unknown"))

    def test_unprofitable_promo_stays_off(self):
        board = discord_post.promos_board([promo("a", 1.5)])
        self.assertEqual(board["embeds"][0]["fields"][0]["name"], "Nothing right now")

    def test_shows_what_you_would_keep(self):
        value = discord_post.promos_board([promo("a", 23)])["embeds"][0]["fields"][0]["value"]
        self.assertIn("You'd keep about $", value)

    def test_says_when_there_is_no_price_yet(self):
        value = discord_post.promos_board([promo("a", None)])["embeds"][0]["fields"][0]["value"]
        self.assertIn("No resale price yet", value)

    def test_upcoming_promo_shows_its_start(self):
        value = discord_post.promos_board([promo("a", None, start="2026-11-20")])["embeds"][0]["fields"][0]["value"]
        self.assertIn("Starts Nov 20", value)

    def test_big_list_fits_discord_limits(self):
        many = [promo(str(n), 30, card="A very long promo name " * 12) for n in range(40)]
        embed = discord_post.promos_board(many)["embeds"][0]
        self.assertLessEqual(len(embed["fields"]), 25)
        total = len(embed["title"]) + len(embed["description"]) + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
        self.assertLessEqual(total, 6000)


class AnnounceTests(unittest.TestCase):
    def test_first_run_only_records(self):
        client, state = FakeClient(), {}
        discord_post.apply_promos(client, state, [promo("a", 23)])
        self.assertEqual(client.sent, [])
        self.assertEqual(state["promos_announced"], ["a"])

    def test_new_promo_pings_once(self):
        client, state = FakeClient(), {"promos_announced": []}
        discord_post.apply_promos(client, state, [promo("a", 23)])
        discord_post.apply_promos(client, state, [promo("a", 23)])
        self.assertEqual(len(client.sent), 1)
        channel, payload = client.sent[0]
        self.assertEqual(channel, "drop-pings")
        self.assertEqual(payload["content"], "@everyone")

    def test_unprofitable_promo_does_not_ping(self):
        client, state = FakeClient(), {"promos_announced": []}
        discord_post.apply_promos(client, state, [promo("a", 1.5)])
        self.assertEqual(client.sent, [])

    def test_list_still_posts_if_the_channel_was_not_ready_before(self):
        client = FakeClient()
        payload = {"embeds": [{"title": "x"}]}
        digest = discord_post.fingerprint(payload)
        state = {"boards": {"free-promos": {"id": None, "hash": digest}}}
        discord_post.update_board(client, state, "free-promos", payload)
        self.assertIn("free-promos", client.boards)


class DataTests(unittest.TestCase):
    def test_every_promo_is_complete(self):
        entries = common.load_json(common.DATA_DIR / "promos.json")["promos"]
        self.assertEqual(len({entry["id"] for entry in entries}), len(entries))
        for entry in entries:
            for field in ("card", "stores", "how", "until", "status", "sources"):
                self.assertTrue(entry[field], f"{entry['id']} needs {field}")
            date.fromisoformat(entry["start"])
            if entry["end"]:
                date.fromisoformat(entry["end"])
            if entry.get("match"):
                re.compile(entry["match"])
            self.assertTrue(all(source["url"].startswith("https://") for source in entry["sources"]))

    def test_every_promo_can_be_displayed(self):
        today = TODAY
        for entry in promos.load_active(today):
            discord_post.promo_field(promos.priced(entry, None, COSTS, today))


if __name__ == "__main__":
    unittest.main()
