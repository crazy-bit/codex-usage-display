"""Ask the running BLE sync process to change the display's saved volume."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid
from app_paths import data_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('volume', type=int, help='0 (mute) to 100')
    parser.add_argument('--preview', action='store_true', help='Play the reminder once after setting volume')
    args = parser.parse_args()
    if not 0 <= args.volume <= 100:
        parser.error('volume must be between 0 and 100')
    work = data_dir()
    work.mkdir(exist_ok=True)
    request = {'id': uuid.uuid4().hex, 'volume': args.volume, 'preview': args.preview}
    temporary = work / 'volume-request.tmp'
    temporary.write_text(json.dumps(request), encoding='utf-8')
    temporary.replace(work / 'volume-request.json')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            result = json.loads((work / 'volume-result.json').read_text(encoding='utf-8'))
            if result.get('id') == request['id']:
                print(f'Device confirmed volume={args.volume} (saved)')
                return 0
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    print('No confirmation yet. Start ble_sender.py and check Bluetooth; request remains pending.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
