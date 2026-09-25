"""One public playlist per pillar; ids cached in state/playlists.json."""
import json

import requests

from src import state
from src.upload import access_token

API = "https://www.googleapis.com/youtube/v3"
CACHE = state.ROOT / "state/playlists.json"


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def _cache():
    try:
        return json.loads(CACHE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def ensure(pillar, cfg, tok=None):
    tok = tok or access_token()
    cache = _cache()
    if cache.get(pillar):
        return cache[pillar], tok
    want = cfg["playlists"][pillar]
    # reuse an existing playlist with the same title (e.g. made by hand)
    r = requests.get(f"{API}/playlists", headers=_h(tok), timeout=30,
                     params={"part": "snippet", "mine": "true", "maxResults": 50})
    r.raise_for_status()
    pid = next((p["id"] for p in r.json().get("items", [])
                if p["snippet"]["title"] == want["title"]), None)
    if not pid:
        r = requests.post(f"{API}/playlists", headers=_h(tok), timeout=30,
                          params={"part": "snippet,status"},
                          json={"snippet": {"title": want["title"],
                                            "description": want["description"],
                                            "defaultLanguage": "en"},
                                "status": {"privacyStatus": "public"}})
        r.raise_for_status()
        pid = r.json()["id"]
        print(f"[playlists] created {want['title']} ({pid})")
    cache[pillar] = pid
    CACHE.write_text(json.dumps(cache, indent=1))
    return pid, tok


def add(video_id, pillar, cfg, tok=None):
    pid, tok = ensure(pillar, cfg, tok)
    r = requests.post(f"{API}/playlistItems", headers=_h(tok), timeout=30,
                      params={"part": "snippet"},
                      json={"snippet": {"playlistId": pid, "resourceId": {
                          "kind": "youtube#video", "videoId": video_id}}})
    r.raise_for_status()
    return tok


def backfill(history, cfg):
    """Weekly: make sure every uploaded video sits in its pillar playlist."""
    tok = access_token()
    added = 0
    for pillar in sorted({h["pillar"] for h in history}):
        pid, tok = ensure(pillar, cfg, tok)
        have, page = set(), None
        while True:
            params = {"part": "contentDetails", "playlistId": pid,
                      "maxResults": 50}
            if page:
                params["pageToken"] = page
            r = requests.get(f"{API}/playlistItems", headers=_h(tok),
                             params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            have |= {i["contentDetails"]["videoId"] for i in data["items"]}
            page = data.get("nextPageToken")
            if not page:
                break
        for h in history:
            if h["pillar"] == pillar and h["video_id"] not in have:
                add(h["video_id"], pillar, cfg, tok)
                added += 1
    return added
