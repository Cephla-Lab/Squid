"""Small persisted UI state for the fluidics tabs (last config/protocol/save-to paths).

Best-effort: a missing or corrupt file is an empty state, a failed write is logged and ignored."""

import json
import os

import squid.logging

_log = squid.logging.get_logger(__name__)

STATE_FILE = "cache/fluidics_protocol.json"


def load_ui_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ui_state(path: str = STATE_FILE, **updates) -> dict:
    state = load_ui_state(path)
    state.update({k: v for k, v in updates.items() if v is not None})
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        _log.warning(f"Could not save fluidics UI state: {e}")
    return state
