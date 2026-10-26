import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from usage import find_codex, normalize, normalize_window


class DiscoveryTests(unittest.TestCase):
    @patch('usage.shutil.which', return_value='path-codex.exe')
    def test_path_has_priority(self, which):
        self.assertEqual(find_codex(), 'path-codex.exe')

    @patch('usage.shutil.which', return_value=None)
    def test_desktop_fallback_without_path(self, which):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'OpenAI' / 'Codex' / 'bin'
            old = root / 'old' / 'codex.exe'
            new = root / 'new' / 'codex.exe'
            for path in (old, new):
                path.parent.mkdir(parents=True)
                path.touch()
            os.utime(old, (100, 100))
            os.utime(new, (200, 200))
            with patch.dict(os.environ, {'LOCALAPPDATA': directory}):
                self.assertEqual(find_codex(), str(new))

    @patch('usage.shutil.which', return_value=None)
    def test_explicit_missing_does_not_fallback(self, which):
        self.assertIsNone(find_codex('missing.exe'))
        which.assert_called_once_with('missing.exe')

    @patch('usage.shutil.which', return_value=None)
    def test_missing_installation(self, which):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'LOCALAPPDATA': directory}):
                self.assertIsNone(find_codex())


class QuotaTests(unittest.TestCase):
    def test_multiple_buckets_and_windows(self):
        result = normalize({'rateLimitsByLimitId': {
            'codex': {'primary': {'usedPercent': 7}, 'secondary': {'usedPercent': 90}},
            'other': {'primary': {'usedPercent': 20}},
        }})
        self.assertEqual(len(result['buckets']), 2)
        self.assertEqual(result['buckets'][0]['primary']['remainingPercent'], 93)
        self.assertEqual(result['buckets'][0]['secondary']['remainingPercent'], 10)

    def test_legacy_fallback(self):
        result = normalize({'rateLimitsByLimitId': {}, 'rateLimits': {'primary': {'usedPercent': 25}}})
        self.assertEqual(result['buckets'][0]['primary']['remainingPercent'], 75)

    def test_unknown_is_not_zero(self):
        for used in (None, '7', True, float('nan'), float('inf')):
            self.assertIsNone(normalize_window({'usedPercent': used})['remainingPercent'])
        self.assertIsNone(normalize_window(None))
        self.assertEqual(normalize({})['buckets'], [])

    def test_clamp(self):
        self.assertEqual(normalize_window({'usedPercent': 110})['remainingPercent'], 0)
        self.assertEqual(normalize_window({'usedPercent': -10})['remainingPercent'], 100)


if __name__ == '__main__':
    unittest.main()


class ResetCreditsTests(unittest.TestCase):
    def test_available_unexpired_credits_only(self):
        from usage import normalize_reset_credits
        credits=[{'status':'available','resetType':'codexRateLimits','expiresAt':t,'id':'private'} for t in (100,300,200)]
        credits.append({'status':'used','resetType':'codexRateLimits','expiresAt':400})
        result=normalize_reset_credits({'credits':credits},now=150)
        self.assertEqual(result,{'availableCount':2,'expiresAt':200})
        self.assertNotIn('private',str(result))
        self.assertIsNone(normalize_reset_credits(None))
        self.assertEqual(normalize_reset_credits({'credits':[]}),{'availableCount':0,'expiresAt':None})
