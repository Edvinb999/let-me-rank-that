"""Claude writes the narration around fixed, verified numbers.

Guard: every number that appears in the narration must be one of the numbers
we supplied (or a rank 1..N). Anything else -> the attempt is rejected.
"""
import json
import os
import re

import anthropic

SYSTEM = """You write narration for "Let Me Rank That", a YouTube Shorts channel.
The host is a dry, quick, slightly opinionated narrator counting a ranking down
from #{n} to #1. It must sound like a person talking, not a list being read.

The whole narration is spoken as ONE continuous take over a ~25 second video.
The graphic already shows every name, value and rank, so the voice must ADD
something: the comparison in "note", the reference point, a contrast between
neighbours, or a dry remark. Never just read out the value on screen.

HARD RULES
- Use ONLY facts in the data. Never add a number, year, statistic or claim that
  is not in it. Any number you use must be written exactly as in the data.
- Do not invent reasons why something ranks where it does. Opinions and light
  jokes are fine; fake facts are not. Never mock a country or its people.
- Hook: max 10 words. Give the viewer a stake, ideally using the reference
  (e.g. what it costs where they likely live). No "welcome", no "today".
  Work in "let me rank that" naturally, or skip it if it does not fit.
- Each item line: max 16 words, one or two short sentences, names the item,
  starts directly with the item (the rank is shown on screen, never say it).
  Vary sentence shapes; no two lines may start the same way.
- The #1 line should land as the payoff.
- Outro: ONE short question (max 9 words) that invites a comment.
- YouTube title: max 60 characters, curiosity without lies, no emojis/hashtags.
- Description: 2 short plain sentences. No source (appended automatically).

Reply with ONLY a JSON object, no markdown fences:
{{"hook": str, "lines": [str x {n}, in order #{n} down to #1], "outro": str,
  "yt_title": str, "description": str, "tags": [5-8 short strings]}}"""

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(num):
    return num.replace(",", "")


def _allowed_numbers(ranking):
    allowed = {str(i) for i in range(1, len(ranking.items) + 1)}
    for it in ranking.items:
        for m in NUM_RE.findall(it.display):
            allowed.add(_norm(m))
        for m in NUM_RE.findall(it.note):
            allowed.add(_norm(m))
    ref = ranking.reference or {}
    for m in NUM_RE.findall(ranking.title + " " + ranking.subtitle + " "
                            + ref.get("display", "")):
        allowed.add(_norm(m))
    return allowed


def _check(data, ranking):
    n = len(ranking.items)
    if not isinstance(data.get("lines"), list) or len(data["lines"]) != n:
        return f"expected {n} lines"
    for k in ("hook", "outro", "yt_title", "description"):
        if not isinstance(data.get(k), str) or not data[k].strip():
            return f"missing {k}"
    if len(data["yt_title"]) > 70:
        return "title too long"
    allowed = _allowed_numbers(ranking)
    spoken = [data["hook"], data["outro"], *data["lines"]]
    for text in spoken + [data["yt_title"], data["description"]]:
        for m in NUM_RE.findall(text):
            if _norm(m) not in allowed:
                return f"unsupported number {m!r} in: {text}"
    # each line must mention its item (lines are #n..#1, items[0] is #1)
    for line, item in zip(data["lines"], reversed(ranking.items)):
        key = item.name.split(",")[0].split("(")[0].strip().lower()
        if key not in line.lower():
            return f"line does not name {item.name!r}: {line}"
    return None


def _parse(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def write(ranking, cfg):
    n = len(ranking.items)
    payload = {
        "title": ranking.title,
        "subtitle": ranking.subtitle,
        "what_the_number_means": ranking.unit_hint,
        "reference": ranking.reference or None,
        "countdown": [
            {"rank": n - i, "name": it.name, "display": it.display,
             "note": it.note}
            for i, it in enumerate(reversed(ranking.items))
        ],
    }
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    sc = cfg["script"]
    feedback = ""
    for attempt in range(1, sc["attempts"] + 1):
        msg = client.messages.create(
            model=sc["model"], max_tokens=sc["max_tokens"],
            system=SYSTEM.format(n=n),
            messages=[{"role": "user", "content":
                       "DATA:\n" + json.dumps(payload, ensure_ascii=False,
                                               indent=1) + feedback}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = _parse(text)
            problem = _check(data, ranking)
        except Exception as e:  # noqa: BLE001
            problem = f"invalid JSON ({e})"
        if not problem:
            return data
        print(f"[script] attempt {attempt} rejected: {problem}")
        feedback = (f"\n\nYour previous reply was rejected: {problem}. "
                    "Fix it and follow the HARD RULES exactly.")
    raise RuntimeError("Could not get a valid script")
