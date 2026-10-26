"""Free reset announcements. Data: https://codex-reset.com/."""
import asyncio
import datetime
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from app_paths import data_dir, write_json


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def settings():
    raw = read_json(data_dir() / 'reset-settings.json', {})
    interval = raw.get('interval', 300)
    return {'enabled': raw.get('enabled', True) is True, 'revision': raw.get('revision'),
            'interval': max(60, min(3600, interval)) if type(interval) is int else 300}


class ResetAPIError(RuntimeError):
    def __init__(self, message, retry_after=300):
        super().__init__(message)
        self.retry_after = retry_after


class ResetClient:
    def get(self, endpoint):
        request = urllib.request.Request('https://codex-reset.com/api/' + endpoint, headers={
            'User-Agent': 'CodexUsageDisplay/1.0 (+https://github.com/crazy-bit/codex-usage-display)',
            'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                delay = max(300, int(error.headers.get('Retry-After', '300')))
            except ValueError:
                delay = 300
            raise ResetAPIError(f'免费重置数据请求失败（HTTP {error.code}）', delay) from None
        except (urllib.error.URLError, TimeoutError):
            raise ResetAPIError('无法连接免费重置数据源，请检查网络。') from None
        except (ValueError, UnicodeError):
            raise ResetAPIError('重置数据格式无效。') from None
        if not isinstance(result, dict):
            raise ResetAPIError('重置记录不完整，本次不更新基线。')
        return result

    def timeline(self):
        result = self.get('timeline')
        if not isinstance(result.get('events'), list):
            raise ResetAPIError('重置记录不完整，本次不更新基线。')
        return result

    def forecast(self):
        result = self.get('forecast')
        probabilities = result.get('probabilities', {})
        signal = result.get('official_signal')
        signal_probability = probabilities.get('signal_percent')
        signal_active = isinstance(signal, dict) and trusted_tibo_url(signal.get('url'))
        use_signal = signal_active and type(signal_probability) in (int,float) and 0 <= signal_probability <= 100
        probability = signal_probability if use_signal else probabilities.get('rounded_48h')
        if type(probability) not in (int, float) or not 0 <= probability <= 100:
            raise ResetAPIError('48 小时重置概率无效。')
        try:
            updated = datetime.datetime.fromisoformat(result['updated_at'].replace('Z', '+00:00'))
            if updated.tzinfo is None or not 0 <= time.time() - updated.timestamp() <= 1800:
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ResetAPIError('重置概率已过期，等待数据源更新。') from None
        # Only absolute future timestamps from an explicit announcement window.
        # Historical time_window is a recurring pattern, never a next-reset date.
        next_reset = None
        signal = result.get('official_signal')
        window = signal.get('window') if isinstance(signal, dict) else None
        candidate = window.get('start_at') if isinstance(window, dict) else window
        if isinstance(candidate, str):
            try:
                reset = datetime.datetime.fromisoformat(candidate.replace('Z', '+00:00'))
                if reset.tzinfo is not None and time.time() < reset.timestamp() <= time.time() + 7 * 86400:
                    next_reset = int(reset.timestamp())
            except ValueError:
                pass
        return {'p': probability, 't': int(updated.timestamp()), 'nr': next_reset,
            'kind':'signal' if use_signal else 'model', 'model48':probabilities.get('rounded_48h')}




def trusted_tibo_url(url):
    import re
    return isinstance(url,str) and re.fullmatch(r'https://(?:x\.com|twitter\.com)/thsottiaux/status/[0-9]+',url) is not None


def tibo_confirmed(record):
    import re
    if not trusted_tibo_url(record.get('url')):
        return False
    text = str(record.get('summary','')).lower()
    if re.search(r"\b(will|tomorrow|not yet|not done|hasn't|haven't|has not|have not|not reset)\b",text) or '?' in text:
        return False
    return bool(re.search(r'\b(done|all propagated|reset completed|reset has been processed|reset is complete)\b',text))


def event_id(identifier):
    return 2_000_000_000 + int.from_bytes(hashlib.blake2s(identifier.encode(), digest_size=4).digest(), 'little') % 1_500_000_000


class ResetMonitor:
    def __init__(self, client=None):
        self.client = client or ResetClient()
        self.path = data_dir() / 'reset-state.json'
        self.state = read_json(self.path, {'initialized': False, 'seen': [], 'pending': []})
        self.lock = threading.RLock()

    def poll(self):
        result = self.client.timeline()
        with self.lock:
            now = time.time()
            seen = set(self.state.get('seen', []))
            pending = [p for p in self.state.get('pending', []) if p['expires'] > now]
            records = []
            preview_seen = set(self.state.get('preview_seen', []))
            for record in result['events']:
                if not isinstance(record, dict) or record.get('group') != 'reset':
                    continue
                confirmed = record.get('announcement_state') == 'announced' or tibo_confirmed(record)
                records.append({**{key: str(record.get(key) or '')[:1500]
                    for key in ('id','announced_at','summary','url')},
                    'confirmed': confirmed})
                identifier = str(record.get('id', ''))
                try:
                    published = datetime.datetime.fromisoformat(record['announced_at'].replace('Z', '+00:00'))
                    if published.tzinfo is None:
                        continue
                    timestamp = published.timestamp()
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue
                if not identifier:
                    continue
                if confirmed:
                    if identifier not in seen and self.state.get('initialized') and 0 <= now-timestamp <= 86400:
                        pending.append({'id': identifier, 'event': event_id(identifier), 'expires':timestamp+86400, 'kind':'confirmed'})
                    seen.add(identifier)
                elif trusted_tibo_url(record.get('url')) and identifier not in preview_seen:
                    if 0 <= now-timestamp <= 86400:
                        key = 'preview:' + identifier
                        pending.append({'id':key, 'event':event_id(key), 'expires':timestamp+86400, 'kind':'preview'})
                    preview_seen.add(identifier)
            self.state.update(initialized=True, seen=sorted(seen), preview_seen=sorted(preview_seen), pending=pending)
            write_json(self.path, self.state)
            write_json(data_dir() / 'reset-status.json', {'state':'ready', 'time':now,
                'source':'https://codex-reset.com/', 'source_updated_at':result.get('updated_at'), 'events':records[:20]})

    def pending(self):
        with self.lock:
            return next((p for p in self.state['pending'] if p['expires'] > time.time()), None)

    def acknowledge(self, identifier):
        with self.lock:
            self.state['pending'] = [p for p in self.state['pending'] if p['id'] != identifier]
            write_json(self.path, self.state)


async def watch(shared):
    monitor = ResetMonitor()
    shared['reset_monitor'] = monitor
    next_poll = 0
    next_forecast = 0
    while True:
        config = settings()
        if time.monotonic() >= next_forecast:
            delay = config['interval']
            try:
                shared['forecast'] = await asyncio.to_thread(monitor.client.forecast)
                write_json(data_dir() / 'forecast-status.json', {'state':'ready', **shared['forecast'],
                    'source':'https://codex-reset.com/'})
            except Exception as error:
                shared.pop('forecast', None)
                write_json(data_dir() / 'forecast-status.json', {'state':'error', 'error':str(error)})
                delay = max(delay, getattr(error, 'retry_after', 300))
            next_forecast = time.monotonic() + delay
        if not config['enabled']:
            write_json(data_dir() / 'reset-status.json', {'state':'disabled', 'time':time.time()})
        elif time.monotonic() >= next_poll:
            delay = config['interval']
            try:
                await asyncio.to_thread(monitor.poll)
            except Exception as error:
                message = str(error) if isinstance(error, ResetAPIError) else '重置通知处理失败，请检查日志。'
                previous = read_json(data_dir() / 'reset-status.json', {})
                write_json(data_dir() / 'reset-status.json', {**previous, 'state':'error', 'time':time.time(), 'error':message})
                delay = max(delay, getattr(error, 'retry_after', 300))
            next_poll = time.monotonic() + delay
        await asyncio.sleep(5)
