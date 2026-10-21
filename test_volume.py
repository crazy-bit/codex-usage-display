import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ble_sender import pending_volume


class VolumeControlTests(unittest.TestCase):
    def test_request_boundaries_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / 'request.json'
            result = Path(directory) / 'result.json'
            with patch('ble_sender.CONTROL', request), patch('ble_sender.RESULT', result):
                self.assertIsNone(pending_volume())
                for value in (0, 80, 100):
                    data = {'id': str(value), 'volume': value}
                    request.write_text(json.dumps(data), encoding='utf-8')
                    self.assertEqual(pending_volume(), data)
                    result.write_text(json.dumps(data), encoding='utf-8')
                    self.assertIsNone(pending_volume())
                for value in (-1, 101, True, 80.5, '80'):
                    request.write_text(json.dumps({'id': 'bad', 'volume': value}), encoding='utf-8')
                    self.assertIsNone(pending_volume())
                request.write_text('{', encoding='utf-8')
                self.assertIsNone(pending_volume())


if __name__ == '__main__':
    unittest.main()
