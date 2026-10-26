"""Send Codex quota to the V2 BLE display. Ctrl+C stops continuous mode."""
import argparse
import asyncio
import json
import time
import hashlib
from pathlib import Path
from bleak import BleakClient, BleakScanner
from ble_protocol import SERVICE, RX, TX, payload, packets, add_forecast
from usage import AppServer, find_codex, normalize
from reset_notice import reset_event
from app_paths import data_dir, write_json
from reset_monitor import watch, settings as reset_settings

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
    reset_watcher = asyncio.create_task(watch(latest))
    try:
        await sync_ble(args, executable, latest)
    finally:
        collector.cancel()
        reset_watcher.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass
        try:
            await reset_watcher
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
                print(f'Display asleep or unavailable; rescan in {backoff}s', flush=True)
                status('sleeping', retrySeconds=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue
            if len(matches) != 1:
                raise RuntimeError('Multiple displays found; specify --address')
            acknowledgements = asyncio.Queue()
            telemetry = {}
            def notified(_, data):
                try:
                    message = json.loads(data)
                    if type(message.get('b')) is int and 0 <= message['b'] <= 6000:
                        telemetry['batteryMv'] = message['b']
                    acknowledgements.put_nowait(message)
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
                    add_forecast(current, latest.get('forecast'))
                    current['i'] = args.interval
                    volume_request = pending_volume()
                    if volume_request:
                        current['vol'] = volume_request['volume']
                    event = int(time.time()) if getattr(args, 'test_voice', False) else (
                        None if getattr(args, 'no_voice', False) else reset_event(previous, current))
                    reset_notice_event = None
                    reset_monitor = latest.get('reset_monitor')
                    if reset_monitor and reset_settings()['enabled'] and not getattr(args, 'no_voice', False):
                        reset_notice_event = await asyncio.to_thread(reset_monitor.pending)
                        if reset_notice_event:
                            event = reset_notice_event['event']
                            if reset_notice_event.get('kind') == 'preview': current['ak'] = 1
                    if volume_request and volume_request.get('preview'):
                        event = int(time.time())
                        reset_notice_event = None
                        current.pop('ak', None)
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
                    print(f'Acknowledged seq={seq} battery={telemetry.get("batteryMv", "--")}mV: {wire.decode().strip()}', flush=True)
                    status('synced', address=matches[0].address, windows=current['w'], fetchedAt=current['at'],
                           batteryMv=telemetry.get('batteryMv'))
                    if reset_notice_event:
                        await asyncio.to_thread(reset_monitor.acknowledge, reset_notice_event['id'])
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
                        if reset_monitor and reset_settings()['enabled'] and not getattr(args, 'no_voice', False):
                            if await asyncio.to_thread(reset_monitor.pending):
                                break
                raise RuntimeError('BLE disconnected')
        except Exception as error:
            if args.once:
                raise
            print(f'{type(error).__name__}: {error}; retry in {backoff}s', flush=True)
            status('retrying', error=f'{type(error).__name__}: {error}', retrySeconds=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--address')
    parser.add_argument('--limit', default='codex')
    parser.add_argument('--codex')
    parser.add_argument('--test-voice', action='store_true', help='Play the built-in reminder once (implies --once)')
    parser.add_argument('--no-voice', action='store_true', help='Disable automatic reset announcements')
    args = parser.parse_args()
    if args.test_voice:
        args.once = True
    if args.interval < 10 or args.interval > 600:
        parser.error('--interval must be between 10 and 600 seconds')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')


if __name__ == '__main__':
    main()
