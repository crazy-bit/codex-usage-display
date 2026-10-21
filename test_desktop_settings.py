import unittest
from desktop_settings import validated, DEFAULTS


class SettingsTests(unittest.TestCase):
    def test_invalid_values_are_not_applied(self):
        for value in (None, [], {'volume': -1}, {'volume': 101}, {'volume': True}, {'volume': '80'}):
            self.assertEqual(validated(value)['volume'], 80)
        self.assertEqual(validated({'provider': 'unknown'})['provider'], 'Codex')
        self.assertEqual(validated({'monitor': 'false'})['monitor'], True)

    def test_valid_settings_roundtrip(self):
        value = {'provider': 'Codex', 'monitor': False, 'voice': False, 'volume': 0, 'address': 'AC:27:6E:D3:76:BD'}
        self.assertEqual(validated(value), value)
        self.assertEqual(validated({'address': 'bad'})['address'], '')
        self.assertEqual(validated({}), DEFAULTS)


if __name__ == '__main__':
    unittest.main()
