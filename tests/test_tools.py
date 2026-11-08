import unittest

from tools import trigger_refresh


class TriggerTests(unittest.TestCase):
    def test_starts_only_the_feed_workflow(self):
        self.assertEqual(
            trigger_refresh.command("gh"),
            ["gh", "workflow", "run", "feed.yml", "--repo", "osphantom/pokemon-card-feed"],
        )


if __name__ == "__main__":
    unittest.main()
