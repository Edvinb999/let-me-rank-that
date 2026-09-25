"""Weekly manager: YouTube Analytics -> scores -> learned weights -> report.

Score per video = views per day (first 14 days) x retention factor.
Groups (pillar, topic, voice) with enough mature videos get a new weight:
    target = group mean score / overall mean score   (clipped)
    new    = old * smoothing + target * (1 - smoothing)
Writes reports/<date>.md and opens a GitHub issue labelled weekly-report.
"""
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

import requests
import yaml

from src import state
from src.upload import access_token

ROOT = Path(__file__).resolve().parent.parent


def analytics(video_ids, start, end):
    tok = access_token()
    out = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        r = requests.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            headers={"Authorization": f"Bearer {tok}"},
            params={"ids": "channel==MINE", "startDate": start,
                    "endDate": end, "dimensions": "video",
                    "metrics": ("views,averageViewPercentage,"
                                "averageViewDuration,likes,subscribersGained"),
                    "filters": "video==" + ",".join(batch),
                    "maxResults": 200, "sort": "-views"},
            timeout=60)
        r.raise_for_status()
        data = r.json()
        cols = [c["name"] for c in data.get("columnHeaders", [])]
        for row in data.get("rows", []) or []:
            rec = dict(zip(cols, row))
            out[rec["video"]] = rec
    return out


def run():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    mc = cfg["manager"]
    hist = state.history()
    w = state.weights()
    today = dt.date.today()
    if not hist:
        print("[manager] no uploads yet")
        return
    first = min(h["published"][:10] for h in hist)
    stats = analytics([h["video_id"] for h in hist], first, today.isoformat())

    scored = []
    for h in hist:
        pub = dt.date.fromisoformat(h["published"][:10])
        age = (today - pub).days
        s = stats.get(h["video_id"], {})
        views = int(s.get("views", 0))
        ret = float(s.get("averageViewPercentage", 0) or 0)
        h_out = {**h, "age": age, "views": views, "retention": ret,
                 "likes": int(s.get("likes", 0)),
                 "subs": int(s.get("subscribersGained", 0))}
        if age >= mc["min_age_days"]:
            vpd = views / max(1, min(age, 14))
            # retention factor: 1.0 at 70% average viewed, capped 0.5..1.5
            h_out["score"] = vpd * min(1.5, max(0.5, ret / 70.0))
        scored.append(h_out)

    mature = [x for x in scored if "score" in x]
    changes = []
    if mature:
        overall = mean(x["score"] for x in mature) or 1e-9
        for key in ("pillar", "topic", "voice"):
            field = "topic_id" if key == "topic" else key
            groups = defaultdict(list)
            for x in mature:
                groups[x[field]].append(x["score"])
            for g, vals in groups.items():
                if len(vals) < mc["min_videos"]:
                    continue
                target = min(mc["weight_max"],
                             max(mc["weight_min"], mean(vals) / overall))
                old = w[key].get(g, 1.0)
                new = round(old * mc["smoothing"]
                            + target * (1 - mc["smoothing"]), 3)
                w[key][g] = new
                changes.append((key, g, old, new, len(vals), mean(vals)))
    w["updated"] = today.isoformat()
    state.save_weights(w)

    # ------------------------------------------------------------ report --
    week_ago = today - dt.timedelta(days=7)
    this_week = [x for x in scored
                 if dt.date.fromisoformat(x["published"][:10]) > week_ago]
    lines = [f"# Let Me Rank That — weekly report {today}", ""]
    tv = sum(x["views"] for x in scored)
    ts = sum(x["subs"] for x in scored)
    lines += [f"- Uploads total: {len(scored)} (this week: {len(this_week)})",
              f"- Views total: {tv:,}",
              f"- Subscribers gained (all videos): {ts}",
              f"- Subscribers per 1,000 views: "
              f"{(ts / tv * 1000) if tv else 0:.2f}", ""]
    lines += ["## By pillar", "",
              "| Pillar | Videos | Views | Avg retention | Subs / 1,000 views |",
              "|---|---:|---:|---:|---:|"]
    for pil in sorted({x["pillar"] for x in scored}):
        grp = [x for x in scored if x["pillar"] == pil]
        v = sum(x["views"] for x in grp)
        sb = sum(x["subs"] for x in grp)
        rets = [x["retention"] for x in grp if x["views"]]
        lines.append(f"| {pil} | {len(grp)} | {v:,} | "
                     f"{(mean(rets) if rets else 0):.0f}% | "
                     f"{(sb / v * 1000) if v else 0:.2f} |")
    lines.append("")
    lines += ["## Top videos", "", "| Views | Retention | Title | Pillar | Voice |",
              "|---:|---:|---|---|---|"]
    for x in sorted(scored, key=lambda x: -x["views"])[:8]:
        lines.append(f"| {x['views']:,} | {x['retention']:.0f}% | "
                     f"{x['title']} | {x['pillar']} | {x['voice']} |")
    lines += ["", "## Weight changes", ""]
    if changes:
        lines += ["| Type | Name | Old | New | Videos | Avg score |",
                  "|---|---|---:|---:|---:|---:|"]
        for k, g, o, nw, cnt, sc in changes:
            lines.append(f"| {k} | {g} | {o:.2f} | {nw:.2f} | {cnt} | {sc:.1f} |")
    else:
        lines.append("Not enough mature videos yet; weights unchanged.")
    lines += ["", "Score = views/day (first 14 days) × retention factor. "
              "Weights are clipped to "
              f"{mc['weight_min']}–{mc['weight_max']} and smoothed."]
    try:
        from src import playlists
        n_added = playlists.backfill(hist, cfg)
        lines += ["", f"Playlists: {n_added} video(s) added to their pillar "
                  "playlist this week."]
    except Exception as e:  # noqa: BLE001
        lines += ["", f"Playlists: backfill failed ({e})."]
    report = "\n".join(lines)
    rp = ROOT / "reports" / f"{today}.md"
    rp.parent.mkdir(exist_ok=True)
    rp.write_text(report)
    print(report)

    repo, gh = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if repo and gh:
        r = requests.post(f"https://api.github.com/repos/{repo}/issues",
                          headers={"Authorization": f"Bearer {gh}",
                                   "Accept": "application/vnd.github+json"},
                          data=json.dumps({
                              "title": f"Let Me Rank That weekly report {today}",
                              "body": report, "labels": ["weekly-report"]}),
                          timeout=30)
        print(f"[manager] issue: {r.status_code}")


if __name__ == "__main__":
    run()
