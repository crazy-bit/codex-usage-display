"""Stable writable state location, including for a single-file EXE."""
import os
from pathlib import Path
import sys


def data_dir():
    override = os.environ.get('CODEX_USAGE_DATA_DIR')
    if override:
        return Path(override)
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CodexUsageDisplay'
    return Path(__file__).parent / 'work'


def write_json(path, value):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
