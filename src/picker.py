"""Chooses what to publish next, using learned weights (state/weights.json).

Rules: never the same pillar twice in a row, never a topic used in the last
`topic_cooldown` uploads, voices are A/B tested by weight.
"""
import random

from src import state, topics


def _wchoice(options, wmap, base=None):
    base = base or {}
    ws = [max(0.05, base.get(o, 1.0) * wmap.get(o, 1.0)) for o in options]
    return random.choices(options, weights=ws, k=1)[0]


def pick(cfg, n):
    hist = state.history()
    w = state.weights()
    sel = cfg["selection"]
    recent = [h["topic_id"] for h in hist[-sel["topic_cooldown"]:]]
    last_pillar = hist[-1]["pillar"] if hist else None

    built = {}
    for tid, f in topics.TOPICS.items():
        if tid in recent:
            continue
        try:
            built[tid] = f(n)
        except Exception as e:  # noqa: BLE001  (a data source can be down)
            print(f"[select] skip {tid}: {e}")
    if not built:  # everything on cooldown: allow anything except the last one
        last = hist[-1]["topic_id"] if hist else None
        built = {t: f(n) for t, f in topics.TOPICS.items() if t != last}

    pillars = sorted({r.pillar for r in built.values()})
    if len(pillars) > 1 and last_pillar in pillars:
        pillars.remove(last_pillar)
    pillar = _wchoice(pillars, w["pillar"], sel.get("pillar_base"))
    cands = [t for t, r in built.items() if r.pillar == pillar]
    good = [t for t in cands
            if built[t].spread() >= topics.MIN_SPREAD or built[t].reference]
    tid = _wchoice(good or cands, w["topic"])

    voices = cfg["voices"]
    vname = _wchoice([v["name"] for v in voices], w["voice"])
    voice = next(v for v in voices if v["name"] == vname)
    return built[tid], voice
