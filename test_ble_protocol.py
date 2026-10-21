import json
import unittest
import struct
from ble_protocol import payload, packets
from usage import normalize


class ProtocolTests(unittest.TestCase):
    def test_fragment_offsets_and_reassembly(self):
        wire = b'x' * 1023 + b'\n'
        chunks = packets(wire, 999999)
        reconstructed = bytearray()
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 20)
            seq, offset = struct.unpack('<IH', chunk[:6])
            self.assertEqual(seq, 999999)
            self.assertEqual(offset, len(reconstructed))
            reconstructed.extend(chunk[6:])
        self.assertEqual(reconstructed, wire)

    def test_two_windows_and_unknown_values(self):
        snapshot = normalize({'rateLimits': {'limitId': 'codex',
            'primary': {'usedPercent': 8, 'windowDurationMins': 10080, 'resetsAt': 1793082740},
            'secondary': {'usedPercent': None, 'windowDurationMins': 300}}})
        wire = payload(snapshot, 42)
        self.assertTrue(wire.endswith(b'\n'))
        self.assertLessEqual(len(wire), 1024)
        message = json.loads(wire)
        self.assertEqual(message['s'], 42)
        self.assertEqual(message['w'][0]['r'], 92)
        self.assertIsNone(message['w'][1]['r'])
        self.assertNotIn('accountId', message)

    def test_missing_bucket_is_error(self):
        with self.assertRaises(ValueError):
            payload(normalize({}), 42)

    def test_empty_windows_is_error(self):
        with self.assertRaises(ValueError):
            payload(normalize({'rateLimits': {'limitId': 'codex'}}), 42)


if __name__ == '__main__':
    unittest.main()
