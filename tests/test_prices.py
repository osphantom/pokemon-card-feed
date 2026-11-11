import unittest
from datetime import datetime, timezone

from pipeline import discord_post

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self):
        self.sent = []
        self.boards = {}

    def send(self, channel, payload):
        self.sent.append((channel, payload))

    def upsert(self, channel, payload, message_id):
        self.boards[channel] = payload
        return message_id or "id"


def product(pid, market, current=True, profit=30.0):
    return {"id": pid, "name": f"Box {pid}", "market": market, "msrp": 49.99, "current": current, "profit": profit, "url": "https://x"}


class PriceMoveTests(unittest.TestCase):
    def test_first_run_only_records(self):
        client, state = FakeClient(), {}
        discord_post.apply_price_moves(client, state, [product(1, 100.0)], NOW)
        self.assertEqual(client.sent, [])
        self.assertEqual(state["price_seen"], {"1": 100.0})

    def test_a_big_jump_posts_to_market_prices(self):
        client, state = FakeClient(), {"price_seen": {"1": 100.0}}
        discord_post.apply_price_moves(client, state, [product(1, 130.0)], NOW)
        self.assertEqual(client.sent[0][0], "market-prices")
        self.assertIn("$100.00 → $130.00", client.sent[0][1]["embeds"][0]["description"])
        self.assertEqual(state["price_seen"]["1"], 130.0)

    def test_a_small_move_stays_quiet(self):
        client, state = FakeClient(), {"price_seen": {"1": 100.0}}
        discord_post.apply_price_moves(client, state, [product(1, 105.0)], NOW)
        self.assertEqual(client.sent, [])
        self.assertEqual(state["price_seen"]["1"], 105.0)

    def test_a_drop_posts_too(self):
        client, state = FakeClient(), {"price_seen": {"1": 100.0}}
        discord_post.apply_price_moves(client, state, [product(1, 80.0)], NOW)
        self.assertIn("🔻", client.sent[0][1]["embeds"][0]["description"])

    def test_older_sets_are_left_out(self):
        client, state = FakeClient(), {"price_seen": {"1": 100.0}}
        discord_post.apply_price_moves(client, state, [product(1, 200.0, current=False)], NOW)
        self.assertEqual(client.sent, [])

    def test_the_biggest_mover_comes_first(self):
        seen = {"1": 100.0, "2": 100.0}
        moves = discord_post.price_moves([product(1, 120.0), product(2, 160.0)], seen)
        self.assertEqual(moves[0]["new"], 160.0)

    def test_price_moves_never_ping_the_phone(self):
        client, state = FakeClient(), {"price_seen": {"1": 100.0}}
        discord_post.apply_price_moves(client, state, [product(1, 140.0)], NOW)
        self.assertNotIn("content", client.sent[0][1])


if __name__ == "__main__":
    unittest.main()
