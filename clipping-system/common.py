"""Shared helpers: config loading, folders, logging."""
import json
import logging
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RECORDINGS = DATA / "recordings"   # 10-minute .ts chunks + chat logs, one folder per stream session
CLIPS = DATA / "clips"             # finished vertical clips
STATE_FILE = DATA / "state.json"   # which clips are pending / approved / posted

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def log(name):
    return logging.getLogger(name)


def load_config(path=None):
    path = Path(path or os.environ.get("CLIPPER_CONFIG", ROOT / "config.json"))
    if not path.exists():
        sys.exit(f"Config not found: {path}\nCopy config.example.json to config.json and fill it in.")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for d in (RECORDINGS, CLIPS):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def require_tools(*tools):
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        sys.exit(f"Missing required program(s): {', '.join(missing)}. See README.md -> Install.")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"clips": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)
