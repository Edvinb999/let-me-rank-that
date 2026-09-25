"""YouTube upload (resumable) with the channel's refresh token."""
import json
import os

import requests


def access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def upload(video_path, title, description, tags, category="27"):
    tok = access_token()
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": tags[:15], "categoryId": category,
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": "public",
                   "selfDeclaredMadeForKids": False},
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4"},
        data=json.dumps(meta), timeout=60)
    init.raise_for_status()
    with open(video_path, "rb") as f:
        r = requests.put(init.headers["Location"], data=f,
                         headers={"Content-Type": "video/mp4"}, timeout=600)
    r.raise_for_status()
    vid = r.json()["id"]
    print(f"[upload] https://youtube.com/shorts/{vid}")
    return vid
