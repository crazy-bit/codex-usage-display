"""Read Codex account quota through the local app-server (standard library only)."""
import argparse
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def find_codex(explicit=None):
    """Honor explicit paths; otherwise try PATH and the Windows desktop bundle."""
    if explicit is not None:
        return shutil.which(explicit)
    executable = shutil.which('codex')
    if executable:
        return executable
    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        root = Path(local_app_data) / 'OpenAI' / 'Codex' / 'bin'
        candidates = [path for path in root.glob('*/codex.exe') if path.is_file()]
        # Desktop updates use opaque directory names, so compare file times.
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def normalize_window(value):
    if not isinstance(value, dict):
        return None
    used = value.get('usedPercent')
    valid = isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used)
    return {
        'usedPercent': used if valid else None,
        'remainingPercent': max(0, min(100, 100 - used)) if valid else None,
        'windowDurationMins': value.get('windowDurationMins'),
        'resetsAt': value.get('resetsAt'),
    }


def normalize_reset_credits(value, now=None):
    """Expose counts/expiry only; never transmit credit IDs or redemption actions."""
    now = time.time() if now is None else now
    if not isinstance(value, dict) or not isinstance(value.get('credits'), list):
        return None
    credits = []
    for credit in value['credits']:
        if not isinstance(credit, dict) or credit.get('status') != 'available' or credit.get('resetType') != 'codexRateLimits':
            continue
        expiry = credit.get('expiresAt')
        if expiry is not None and (type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry <= now):
            continue
        credits.append(expiry)
    expiries = [t for t in credits if t is not None]
    return {'availableCount':len(credits), 'expiresAt':min(expiries) if expiries else None}


def normalize(result):
    buckets = result.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        legacy = result.get('rateLimits')
        buckets = {legacy.get('limitId') or 'codex': legacy} if isinstance(legacy, dict) else {}
    return {
        'fetchedAt': datetime.now(timezone.utc).isoformat(),
        'resetCredits': normalize_reset_credits(result.get('rateLimitResetCredits')),
        'buckets': [
            {
                'limitId': key,
                'limitName': bucket.get('limitName'),
                'primary': normalize_window(bucket.get('primary')),
                'secondary': normalize_window(bucket.get('secondary')),
                'rateLimitReachedType': bucket.get('rateLimitReachedType'),
            }
            for key, bucket in buckets.items() if isinstance(bucket, dict)
        ],
    }


class AppServer:
    def __init__(self, executable, timeout=30):
        self.executable = executable
        self.timeout = timeout
        self.messages = queue.Queue()
        self.sequence = 0

    def __enter__(self):
        self.process = subprocess.Popen(
            [self.executable, 'app-server'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request('initialize', {'clientInfo': {'name': 'codex_usage_display', 'version': '0.1.0'}})
            self.send({'method': 'initialized', 'params': {}})
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        self.send({'id': request_id, 'method': method, 'params': params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError(f'{method}: timed out after {self.timeout:g}s') from None
            if message is None:
                raise RuntimeError('Codex app-server exited before responding')
            if message.get('id') != request_id:
                continue
            if 'error' in message:
                error = message['error']
                raise RuntimeError(f"{method}: RPC error {error.get('code')}: {error.get('message')}")
            return message.get('result', {})

    def __exit__(self, *_):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', help='Explicit Codex executable name or absolute path (default: auto-detect)')
    parser.add_argument('--timeout', type=float, default=30, help='Timeout per RPC in seconds')
    parser.add_argument('--json', action='store_true', help='Print normalized JSON for hardware integration')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be a positive finite number')
    executable = find_codex(args.codex)
    if not executable:
        parser.exit(1, 'Codex executable not found; install Codex CLI or specify --codex PATH.\n')
    try:
        with AppServer(executable, args.timeout) as server:
            snapshot = normalize(server.request('account/rateLimits/read'))
        if not snapshot['buckets']:
            raise RuntimeError('No quota data returned; verify ChatGPT login with codex login status')
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            print('Codex quota | ' + snapshot['fetchedAt'])
            for bucket in snapshot['buckets']:
                print(bucket['limitName'] or bucket['limitId'])
                for name in ('primary', 'secondary'):
                    window = bucket[name]
                    if window is None:
                        continue
                    remaining = window['remainingPercent']
                    label = f'{remaining:g}%' if remaining is not None else 'unavailable'
                    reset = window['resetsAt']
                    when = datetime.fromtimestamp(reset).astimezone().isoformat() if isinstance(reset, (int, float)) else 'unavailable'
                    print(f"  {name}: remaining {label} | window {window['windowDurationMins']} min | resets {when}")
    except (OSError, RuntimeError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
