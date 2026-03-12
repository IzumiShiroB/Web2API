import json
import os
import time
from pathlib import Path
from datetime import datetime
from typing import Optional


STATE_FILE = Path(__file__).parent / "server_state.json"


def init_state():
    state = {
        "running": False,
        "pid": None,
        "platform": "",
        "started_at": None,
        "shutdown_requested": False,
        "last_update": datetime.now().isoformat()
    }
    _write_state(state)


def _write_state(state: dict):
    state["last_update"] = datetime.now().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _read_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "running": False,
            "pid": None,
            "platform": "",
            "started_at": None,
            "shutdown_requested": False,
            "last_update": None
        }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "running": False,
            "pid": None,
            "platform": "",
            "started_at": None,
            "shutdown_requested": False,
            "last_update": None
        }


def set_server_running(platform: str, pid: int):
    state = {
        "running": True,
        "pid": pid,
        "platform": platform,
        "started_at": datetime.now().isoformat(),
        "shutdown_requested": False,
    }
    _write_state(state)


def set_server_stopped():
    state = _read_state()
    state["running"] = False
    state["pid"] = None
    state["started_at"] = None
    state["shutdown_requested"] = False
    _write_state(state)


def request_shutdown():
    state = _read_state()
    state["shutdown_requested"] = True
    _write_state(state)


def check_shutdown_requested() -> bool:
    state = _read_state()
    return state.get("shutdown_requested", False)


def is_server_running() -> bool:
    state = _read_state()
    if not state.get("running", False):
        return False
    
    pid = state.get("pid")
    if pid is None:
        return False
    
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def get_server_info() -> dict:
    return _read_state()
