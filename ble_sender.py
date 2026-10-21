"""Send Codex quota to the V2 BLE display. Ctrl+C stops continuous mode."""
import argparse
import asyncio
import json
import time
import hashlib
from pathlib import Path
from bleak import BleakClient, BleakScanner
from ble_protocol import SERVICE, RX, TX, payload, packets
from usage import AppServer, find_codex, normalize
from reset_notice import reset_event
from app_paths import data_dir, write_json

CONTROL = data_dir() / 'volume-request.json'
RESULT = CONTROL.with_name('volume-result.json')


def status(state, **details):
    write_json(data_dir() / 'sync-status.json', {'state': state, 'time': time.time(), **details})


def pending_volume():
    try:
        request = json.loads(CONTROL.read_text(encoding='utf-8'))
        if (not isinstance(request, dict) or type(request.get('volume')) is not int
                or not 0 <= request['volume'] <= 100):
            return None
        if RESULT.exists() and json.loads(RESULT.read_text(encoding='utf-8')).get('id') == request.get('id'):
            return None
        return request
    except (OSError, ValueError, AttributeError):
        return None


def read_snapshot(executable):
    with AppServer(executable) as server:
        return normalize(server.request('account/rateLimits/read'))


async def run(args):
    executable = find_codex(args.codex)
    if not executable:
        raise RuntimeError('Codex not found; use --codex PATH')
    latest = {}

    async def collect():
        while True:
            try:
                snapshot = await asyncio.to_thread(read_snapshot, executable)
                # Validate the selected bucket even when the hardware is offline.
                message = json.loads(payload(snapshot, 0, args.limit))
                latest['snapshot'] = snapshot
                write_json(data_dir() / 'quota-status.json', {
                    'state': 'ready', 'time': time.time(), 'windows': message['w']})
            except Exception as error:
                write_json(data_dir() / 'quota-status.json', {
                    'state': 'error', 'time': time.time(), 'error': str(error)})
            await asyncio.sleep(args.interval)

    collector = asyncio.create_task(collect())
    try:
        await sync_ble(args, executable, latest)
    finally:
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass


async def sync_ble(args, executable, latest):
    backoff = 2
    while True:
        try:
            print('Scanning for CodexDisplay...', flush=True)
            status('scanning')
            devices = await BleakScanner.discover(timeout=8, return_adv=True)
            matches = [d for d, adv in devices.values() if SERVICE in [s.lower() for s in adv.service_uuids]
                       and (not args.address or d.address.lower() == args.address.lower())]
            if not matches:
                raise RuntimeError('Display not found; enable Bluetooth and power on the board')
            if len(matches) != 1:
                raise RuntimeError('Multiple displays found; specify --address')
            acknowledgements = asyncio.Queue()
            def notified(_, data):
                try:
                    acknowledgements.put_nowait(json.loads(data))
                except (ValueError, UnicodeError):
                    pass
            async with BleakClient(matches[0]) as client:
                await client.start_notify(TX, notified)
                print(f'Connected: {matches[0].address}', flush=True)
                status('connected', address=matches[0].address)
                state_key = hashlib.sha256((matches[0].address + args.limit).encode()).hexdigest()[:16]
                state_file = data_dir() / f'reset-state-{state_key}.json'
                try:
                    previous = json.loads(state_file.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    previous = None
                while client.is_connected:
                    snapshot = latest.get('snapshot')
                    # Never resend an old cached snapshot as a fresh hardware update.
                    if snapshot is None or time.time() - __import__('datetime').datetime.fromisoformat(snapshot['fetchedAt']).timestamp() > 120:
                        snapshot = await asyncio.to_thread(read_snapshot, executable)
                    seq = time.time_ns() % 1_000_000
                    wire = payload(snapshot, seq, args.limit)
                    current = json.loads(wire)
                    volume_request = pending_volume()
                    if volume_request:
                        current['vol'] = volume_request['volume']
                    event = int(time.time()) if getattr(args, 'test_voice', False) else (
                        None if getattr(args, 'no_voice', False) else reset_event(previous, current))
                    if volume_request and volume_request.get('preview'):
                        event = int(time.time())
                    if event:
                        current['a'] = event
                    wire = (json.dumps(current, separators=(',', ':')) + '\n').encode()
                    # Sequence + byte offset makes duplicate ATT writes harmless.
                    for chunk in packets(wire, seq):
                        await client.write_gatt_char(RX, chunk, response=True)
                    async with asyncio.timeout(15):
                        while True:
                            ack = await acknowledgements.get()
                            if ack.get('s') == seq:
                                if not ack.get('ok'):
                                    raise RuntimeError(f'Device rejected update: {ack}')
                                break
                    print(f'Acknowledged seq={seq}: {wire.decode().strip()}', flush=True)
                    status('synced', address=matches[0].address, windows=current['w'], fetchedAt=current['at'])
                    if volume_request:
                        temp_result = RESULT.with_suffix('.tmp')
                        temp_result.write_text(json.dumps(volume_request), encoding='utf-8')
                        temp_result.replace(RESULT)
                    state_file.parent.mkdir(parents=True, exist_ok=True)
                    temporary = state_file.with_suffix('.tmp')
                    temporary.write_text(json.dumps(current), encoding='utf-8')
                    temporary.replace(state_file)
                    previous = current
                    backoff = 2
                    if args.once:
                        return
                    # Notice disconnects promptly instead of sleeping the whole interval.
                    for _ in range(args.interval):
                        await asyncio.sleep(1)
                        if not client.is_connected:
                            break
                        if pending_volume():
                            break
                raise RuntimeError('BLE disconnected')
        except Exception as error:
            if args.once:
                raise
            print(f'{type(error).__name__}: {error}; retry in {backoff}s', flush=True)
            status('retrying', error=f'{type(error).__name__}: {error}', retrySeconds=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--address')
    parser.add_argument('--limit', default='codex')
    parser.add_argument('--codex')
    parser.add_argument('--test-voice', action='store_true', help='Play the built-in reminder once (implies --once)')
    parser.add_argument('--no-voice', action='store_true', help='Disable automatic reset announcements')
    args = parser.parse_args()
    if args.test_voice:
        args.once = True
    if args.interval < 10 or args.interval > 120:
        parser.error('--interval must be between 10 and 120 seconds (device marks data stale at 5 minutes)')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')


if __name__ == '__main__':
    main()
