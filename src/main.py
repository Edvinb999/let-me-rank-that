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
        "cta": "Subscribe if you already know who takes the top spot.",
        "outro": "Which one surprised you most?",
        "yt_title": f"{r.title}: top {n} ranked",
        "description": "A mock script for layout testing.",
        "tags": ["ranking"],
    }


def build(topic_id=None, mock=False, out_dir="out", publish=False):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    n = cfg["video"]["items"]
    voice = cfg["voices"][0]
    failed = []
    for tries in range(3):
        if topic_id:
            ranking = topics.pick(n, topic_id)
        else:
            from src import picker
            ranking, voice = picker.pick(cfg, n, exclude=failed)
        cfg["voice"].update(voice_name=voice["name"],
                            voice_id=voice["voice_id"])
        print(f"[main] voice: {voice['name']}")
        print(f"[main] topic: {ranking.topic_id} ({ranking.title})")
        for i, it in enumerate(ranking.items, 1):
            print(f"   #{i} {it.name}: {it.display}")
        if mock:
            script = mock_script(ranking)
            break
        from src import script as scriptmod
        try:
            script = scriptmod.write(ranking, cfg)
            break
        except RuntimeError as e:
            print(f"[main] script failed for {ranking.topic_id}: {e}")
            if topic_id:
                raise
            failed.append(ranking.topic_id)
    else:
        raise RuntimeError("No valid script after 3 topics")
    # spoken order: hook, #n..#2, subscribe ask (peak tension), #1, outro
    texts = [script["hook"], *script["lines"][:-1], script["cta"],
             script["lines"][-1], script["outro"]]
    print("[main] narration:\n   " + "\n   ".join(texts))

    from src import tts
    if mock:
        audio, timeline = tts.mock(texts)
    else:
        try:
            audio, timeline = tts.speak(texts, cfg)
        except Exception as e:  # noqa: BLE001
            if voice == cfg["voices"][0]:
                raise
            print(f"[main] voice {voice['name']} failed ({e}); using fallback")
            voice = cfg["voices"][0]
            cfg["voice"].update(voice_name=voice["name"],
                                voice_id=voice["voice_id"])
            audio, timeline = tts.speak(texts, cfg)
    print("[main] reveal times: " + ", ".join(
        f"{s:.1f}s" for s in timeline["seg_starts"]))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    video = render.Renderer(ranking, script, timeline, cfg).render(
        audio, out / f"{ranking.topic_id}.mp4")

    from src import state
    series = cfg["series"][ranking.pillar]
    number = 1 + sum(1 for h in state.history() if h["pillar"] == ranking.pillar)
    title = f"{script['yt_title'].strip()} | {series} #{number}"
    question = script["outro"].strip()
    description = (script["description"].strip() + "\n\n"
                   + ranking.source_long + "\n\n"
                   + "Subscribe for a new ranking every day.\n"
                   + f"#shorts #ranking #{ranking.pillar}\n\n"
                   + question)
    meta = {
        "topic_id": ranking.topic_id,
        "pillar": ranking.pillar,
        "title": title,
        "series": series,
        "series_number": number,
        "description": description,
        "reference": ranking.reference,
        "tags": script.get("tags", []),
        "items": [{"rank": i, "name": it.name, "display": it.display,
                   "note": it.note}
                  for i, it in enumerate(ranking.items, 1)],
        "narration": texts,
        "video": str(video),
        "voice": voice["name"],
    }
    (out / f"{ranking.topic_id}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"[main] done -> {video}")

    if publish:
        import datetime as dt
        from src import playlists, upload
        vid = upload.upload(video, meta["title"], meta["description"],
                            meta["tags"] + ["ranking", "top 5", "shorts"])
        try:
            playlists.add(vid, ranking.pillar, cfg)
        except Exception as e:  # noqa: BLE001  (manager backfills weekly)
            print(f"[main] playlist add failed, manager will retry: {e}")
        hist = state.history()
        hist.append({"video_id": vid, "topic_id": ranking.topic_id,
                     "series_number": number,
                     "pillar": ranking.pillar, "voice": voice["name"],
                     "title": meta["title"],
                     "published": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})
        state.save_history(hist)
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--test", action="store_true",
                    help="render only, never upload")
    ap.add_argument("--publish", action="store_true",
                    help="pick automatically, render and upload to YouTube")
    a = ap.parse_args()
    if a.publish and a.test:
        ap.error("--publish and --test are mutually exclusive")
    build(a.topic, a.mock, publish=a.publish)
