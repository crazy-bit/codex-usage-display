"""Version 1: UTF-8 JSON terminated by LF, max 1024 bytes including LF."""
import json
import struct
from datetime import datetime

SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
RX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'


def packets(wire, seq):
    """6-byte little-endian sequence/offset header + up to 14 payload bytes."""
    if not 0 <= seq <= 999999 or not 0 < len(wire) <= 1024:
        raise ValueError('Invalid sequence or frame length')
    return [struct.pack('<IH', seq, offset) + wire[offset:offset + 14]
            for offset in range(0, len(wire), 14)]


def payload(snapshot, seq, limit='codex'):
    bucket = next((b for b in snapshot['buckets'] if b['limitId'] == limit), None)
    if bucket is None:
        raise ValueError(f'Quota bucket {limit!r} unavailable')
    windows = []
    for key in ('primary', 'secondary'):
        window = bucket.get(key)
        if window is not None:
            windows.append({'r': window['remainingPercent'], 'm': window['windowDurationMins'], 't': window['resetsAt']})
    if not windows:
        raise ValueError('No quota windows returned')
    message = {'v': 1, 's': seq, 'at': int(datetime.fromisoformat(snapshot['fetchedAt']).timestamp()), 'w': windows}
    credits = snapshot.get('resetCredits') if limit == 'codex' else None
    message['rc'] = {'n':credits['availableCount'], 't':credits['expiresAt']} if credits is not None else None
    wire = (json.dumps(message, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(wire) > 1024:
        raise ValueError('BLE payload too long')
    return wire


def add_forecast(message, forecast, now=None):
    """Optional display-only forecast; unavailable or stale values stay unknown."""
    import time
    now = time.time() if now is None else now
    message['app'] = 'Codex'
    message['f'] = None
    if isinstance(forecast, dict):
        p, t = forecast.get('p'), forecast.get('t')
        if (type(p) in (int, float) and 0 <= p <= 100 and type(t) is int
                and 0 <= now - t <= 1800):
            message['f'] = {'p': p, 't': t, 'age': int(now - t), 'k': 1 if forecast.get('kind') == 'signal' else 0}
