"""Let Me Rank That: build one countdown Short.

  python -m src.main --test            # full pipeline, no upload
  python -m src.main --test --mock     # no API calls (silent audio), for layout checks
"""
import argparse
import json
from pathlib import Path

import yaml

from src import render, topics

ROOT = Path(__file__).resolve().parent.parent


def mock_script(r):
    n = len(r.items)
    return {
        "hook": f"You pay {r.reference.get('display', 'a lot')}. Let me rank that.",
        "lines": [f"{it.name}: {it.note}." for it in reversed(r.items)],
        "outro": "Which one surprised you most?",
        "yt_title": f"{r.title}: top {n} ranked",
        "description": "A mock script for layout testing.",
        "tags": ["ranking"],
    }


def build(topic_id=None, mock=False, out_dir="out"):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    n = cfg["video"]["items"]
    ranking = topics.pick(n, topic_id)
    print(f"[main] topic: {ranking.topic_id} ({ranking.title})")
    for i, it in enumerate(ranking.items, 1):
        print(f"   #{i} {it.name}: {it.display}")

    if mock:
        script = mock_script(ranking)
    else:
        from src import script as scriptmod
        script = scriptmod.write(ranking, cfg)
    texts = [script["hook"], *script["lines"], script["outro"]]
    print("[main] narration:\n   " + "\n   ".join(texts))

    from src import tts
    voice, timeline = tts.mock(texts) if mock else tts.speak(texts, cfg)
    print("[main] reveal times: " + ", ".join(
        f"{s:.1f}s" for s in timeline["seg_starts"]))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    video = render.Renderer(ranking, script, timeline, cfg).render(
        voice, out / f"{ranking.topic_id}.mp4")

    description = (script["description"].strip() + "\n\n"
                   + ranking.source_long + "\n\n#shorts #ranking")
    meta = {
        "topic_id": ranking.topic_id,
        "pillar": ranking.pillar,
        "title": script["yt_title"].strip(),
        "description": description,
        "reference": ranking.reference,
        "tags": script.get("tags", []),
        "items": [{"rank": i, "name": it.name, "display": it.display,
                   "note": it.note}
                  for i, it in enumerate(ranking.items, 1)],
        "narration": texts,
        "video": str(video),
    }
    (out / f"{ranking.topic_id}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"[main] done -> {video}")
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--test", action="store_true",
                    help="render only, never upload")
    a = ap.parse_args()
    build(a.topic, a.mock)
