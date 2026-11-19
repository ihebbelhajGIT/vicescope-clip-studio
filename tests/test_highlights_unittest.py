import unittest
from app.highlights import rank_candidates


class HighlightTests(unittest.TestCase):
    def test_multiple_signals_rank_higher(self):
        rows = rank_candidates(40, [(2, .5), (25, .5)], [22, 24],
                               [{"start": 23, "text": "hello"}], window=20)
        self.assertIn(rows[0].start, (10, 20))
        self.assertIn("scene", rows[0].explanation)
        self.assertIn("spoken", rows[0].explanation)

    def test_no_signals_returns_no_candidates(self):
        self.assertEqual(rank_candidates(60, [], [], []), [])

    def test_invalid_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            rank_candidates(float("nan"), [], [], [])


if __name__ == "__main__":
    unittest.main()
