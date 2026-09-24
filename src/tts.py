"""ElevenLabs narration, one request per segment so scenes sync exactly."""
import os
import subprocess

import numpy as np
import requests

API = "https://api.elevenlabs.io/v1"
SR = 44100


def _headers():
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip()}


def _lookup_voice(vc):
    """Only used if the configured voice_id doesn't exist (needs Voices: Read)."""
    r = requests.get(f"{API}/voices", headers=_headers(), timeout=30)
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs key can't read voices (401). Check the "
                           "key, or give it 'Voices: Read' permission.")
    r.raise_for_status()
    want = vc["voice_name"].lower()
    for v in r.json().get("voices", []):
        if v["name"].lower().startswith(want):
            return v["voice_id"]
    raise RuntimeError(f"Voice {vc['voice_name']!r} not found in ElevenLabs")


def _decode(mp3_bytes):
    """mp3 -> mono float32 numpy at SR."""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1",
         "-ar", str(SR), "pipe:1"],
        input=mp3_bytes, capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def speak_all(texts, cfg):
    """Returns list of numpy arrays, one per text."""
    vc = cfg["voice"]
    voice_id = vc.get("voice_id") or _lookup_voice(vc)
    out = []
    for i, text in enumerate(texts):
        r = _tts(voice_id, texts, i, vc)
        if r.status_code == 404 and i == 0:
            voice_id = _lookup_voice(vc)
            r = _tts(voice_id, texts, i, vc)
        if r.status_code == 401:
            raise RuntimeError("ElevenLabs rejected the key (401). Re-copy "
                               "ELEVENLABS_API_KEY; it needs Text to Speech "
                               "access. Detail: " + r.text[:300])
        if not r.ok:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:300]}")
        out.append(_decode(r.content))
    return out


def _tts(voice_id, texts, i, vc):
    text = texts[i]
    return requests.post(
            f"{API}/text-to-speech/{voice_id}",
            headers={**_headers(), "Accept": "audio/mpeg"},
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": vc["model"],
                "voice_settings": {
                    "stability": vc["stability"],
                    "similarity_boost": vc["similarity_boost"],
                    "style": vc["style"],
                    "speed": vc["speed"],
                },
                # keeps delivery consistent from line to line
                "previous_text": texts[i - 1] if i else None,
                "next_text": texts[i + 1] if i + 1 < len(texts) else None,
            },
            timeout=120,
        )
