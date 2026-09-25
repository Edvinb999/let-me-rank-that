"""Persistent state committed back to the repo by the workflows."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "state/history.json"
WEIGHTS = ROOT / "state/weights.json"


def _load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return default


def history():
    return _load(HISTORY, [])


def save_history(h):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(h, indent=1, ensure_ascii=False))


def weights():
    w = _load(WEIGHTS, {})
    for k in ("pillar", "topic", "voice"):
        w.setdefault(k, {})
    return w


def save_weights(w):
    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS.write_text(json.dumps(w, indent=1, ensure_ascii=False))
