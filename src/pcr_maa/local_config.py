"""Explicit first-time local configuration; stable data only, no device access."""
import json
import os
from pathlib import Path

from .daily import validate_config


def save_config(path, value):
    value = validate_config(value)
    if value['version'] != 2:
        raise ValueError('首次保存仅接受 version 2；旧配置仍可读取')
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves any existing user config, even on a race.
    # A failed write remains invalid evidence; no fallback overwrite or execution.
    with path.open('x', encoding='utf-8') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return value
