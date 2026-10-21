"""Detect scheduled quota renewal from two server-confirmed snapshots."""
def reset_event(previous, current):
    if not previous:
        return None
    old = previous.get('w', [])
    now = current.get('at', 0)
    events = []
    for a, b in zip(old, current.get('w', [])):
        # A later reset alone may be a rolling window. Require recovered quota too.
        if (a.get('m') == b.get('m') and isinstance(a.get('t'), (int, float))
                and isinstance(b.get('t'), (int, float)) and 0 < a['t'] <= now
                and b['t'] > a['t'] and isinstance(a.get('r'), (int, float))
                and isinstance(b.get('r'), (int, float)) and (b['r'] > a['r'] or b['r'] >= 100)):
            events.append(int(a['t']))
    return max(events) if events else None
