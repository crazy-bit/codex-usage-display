import unittest
from reset_notice import reset_event


class ResetTests(unittest.TestCase):
    def snapshot(self, remaining, reset, at=200):
        return {'at': at, 'w': [{'r': remaining, 'm': 10080, 't': reset}]}

    def test_confirmed_renewal(self):
        self.assertEqual(reset_event(self.snapshot(5, 100, 90), self.snapshot(99, 300)), 100)
        self.assertEqual(reset_event(self.snapshot(100, 100, 90), self.snapshot(100, 300)), 100)

    def test_startup_and_duplicates_are_quiet(self):
        current = self.snapshot(99, 300)
        self.assertIsNone(reset_event(None, current))
        self.assertIsNone(reset_event(current, current))

    def test_clock_only_or_rolling_window_are_quiet(self):
        self.assertIsNone(reset_event(self.snapshot(5, 100), self.snapshot(4, 100)))
        self.assertIsNone(reset_event(self.snapshot(5, 100), self.snapshot(4, 300)))
        self.assertIsNone(reset_event(self.snapshot(5, 250), self.snapshot(99, 300)))

    def test_missing_and_changed_period_are_quiet(self):
        self.assertIsNone(reset_event(self.snapshot(None, 100), self.snapshot(99, 300)))
        old = self.snapshot(5, 100)
        old['w'][0]['m'] = 300
        self.assertIsNone(reset_event(old, self.snapshot(99, 300)))


if __name__ == '__main__':
    unittest.main()
