import asyncio
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import json
from ble_sender import read_snapshot, run
from usage import normalize


class OfflineCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_reads_quota_before_ble_is_available(self):
        snapshot = normalize({'rateLimits': {'limitId': 'codex', 'primary': {
            'usedPercent': 34, 'windowDurationMins': 10080, 'resetsAt': 1793082740}}})
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            async def offline(*args):
                for _ in range(100):
                    if (home / 'quota-status.json').exists():
                        raise RuntimeError('hardware offline')
                    await asyncio.sleep(0.01)
                self.fail('Quota collection was blocked by BLE')
            with patch('ble_sender.find_codex', return_value='codex'), \
                 patch('ble_sender.read_snapshot', return_value=snapshot), \
                 patch('ble_sender.data_dir', return_value=home), \
                 patch('ble_sender.sync_ble', side_effect=offline):
                with self.assertRaisesRegex(RuntimeError, 'hardware offline'):
                    await run(SimpleNamespace(codex=None, interval=60, limit='codex'))
            status = json.loads((home / 'quota-status.json').read_text())
            self.assertEqual(status['state'], 'ready')
            self.assertEqual(status['windows'][0]['r'], 66)


class CodexUpdateTests(unittest.TestCase):
    def test_each_snapshot_resolves_current_codex_path(self):
        response = {'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 12}}}
        with patch('ble_sender.find_codex', side_effect=['old/codex.exe', 'new/codex.exe']) as find, \
             patch('ble_sender.AppServer') as app_server:
            app_server.return_value.__enter__.return_value.request.return_value = response
            read_snapshot()
            read_snapshot()
        self.assertEqual(find.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in app_server.call_args_list],
            ['old/codex.exe', 'new/codex.exe'],
        )
