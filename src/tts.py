"""ElevenLabs narration as ONE continuous take, with word timings.

Returns (audio, timeline):
  audio    mono float32 numpy at SR
  timeline {"seg_starts": [s0, s1, ...], "words": [(word, start, end, seg)],
            "end": last_word_end}
"""
import base64
import os
import subprocess

import numpy as np
import requests

API = "https://api.elevenlabs.io/v1"
SR = 44100


def _headers():
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip()}


def _decode(mp3_bytes):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1",
         "-ar", str(SR), "pipe:1"],
        input=mp3_bytes, capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def _lookup_voice(name):
    r = requests.get(f"{API}/voices", headers=_headers(), timeout=30)
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs key can't read voices (401): give it "
                           "'Voices: Read' or fix voice_id in config.yaml.")
    r.raise_for_status()
    for v in r.json().get("voices", []):
        if v["name"].lower().startswith(name.lower()):
            return v["voice_id"]
    raise RuntimeError(f"Voice {name!r} not found in ElevenLabs")


def _request(voice_id, text, vc):
    return requests.post(
        f"{API}/text-to-speech/{voice_id}/with-timestamps",
        headers=_headers(),
        params={"output_format": "mp3_44100_128"},
        json={
            "text": text,
            "model_id": vc["model"],
            "voice_settings": {
                "stability": vc["stability"],
                "similarity_boost": vc["similarity_boost"],
                "style": vc["style"],
                "speed": vc["speed"],
                "use_speaker_boost": True,
            },
        },
        timeout=180,
    )


def build_timeline(segments, chars, starts, ends):
    """Map each segment and each word onto the character timings."""
    joined = "".join(chars)
    bounds, cursor = [], 0
    for seg in segments:
        idx = joined.find(seg.strip()[:12], cursor)
        if idx < 0:
            idx = cursor
        bounds.append(idx)
        cursor = idx + 1
    bounds.append(len(joined))
    seg_starts, words = [], []
    for si in range(len(segments)):
        a, b = bounds[si], bounds[si + 1]
        seg_starts.append(starts[min(a, len(starts) - 1)])
        i = a
        while i < b:
            while i < b and chars[i].isspace():
                i += 1
            j = i
            while j < b and not chars[j].isspace():
                j += 1
            if j > i:
                words.append(("".join(chars[i:j]), starts[i], ends[j - 1], si))
            i = j
    seg_starts[0] = 0.0
    return {"seg_starts": seg_starts, "words": words,
            "end": words[-1][2] if words else ends[-1]}


def speak(segments, cfg):
    vc = cfg["voice"]
    text = " ".join(s.strip() for s in segments)
    voice_id = vc.get("voice_id") or _lookup_voice(vc["voice_name"])
    r = _request(voice_id, text, vc)
    if r.status_code == 404:
        voice_id = _lookup_voice(vc["voice_name"])
        r = _request(voice_id, text, vc)
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the key (401). Detail: "
                           + r.text[:300])
    if not r.ok:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
    data = r.json()
    audio = _decode(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or data.get("normalized_alignment")
    tl = build_timeline(segments, al["characters"],
                        al["character_start_times_seconds"],
                        al["character_end_times_seconds"])
    return audio, tl


def mock(segments, wps=3.0):
    """Timing estimate without API calls (layout tests)."""
    chars, starts, ends, t = [], [], [], 0.15
    text = " ".join(s.strip() for s in segments)
    for ch in text:
        dur = 0.0 if ch == " " else 1 / (wps * 5.2)
        if ch in ".?!":
            dur += 0.25
        chars.append(ch)
        starts.append(t)
        ends.append(t + dur)
        t += dur
    tl = build_timeline(segments, chars, starts, ends)
    return np.zeros(int((tl["end"] + 0.5) * SR), dtype=np.float32), tl
