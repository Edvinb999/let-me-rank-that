"""Claude writes the narration around fixed, verified numbers.

Guard: every number that appears in the narration must be one of the numbers
we supplied (or a rank 1..N). Anything else -> the attempt is rejected.
"""
import json
import os
import re

import anthropic

SYSTEM = """You write narration for "Let Me Rank That", a YouTube Shorts channel.
The host is a dry, confident, slightly opinionated narrator who counts rankings
down from #{n} to #1. Catchphrase for the hook: some natural variation of
"let me rank that". The tone is witty but never mean about countries or people.

HARD RULES
- Use ONLY the facts given. Never add any number, statistic, year, date,
  percentage or claim that is not in the data. If you mention a value, write it
  exactly as given in "display".
- Do not invent reasons *why* something ranks where it does unless the note
  field supplies it. Opinions and jokes are fine; fake facts are not.
- Each item line: 1-2 short sentences, max 22 words, must name the item.
  Lines are spoken while the item is on screen, so do not say "number five" -
  the rank is shown visually; start directly with the item.
- Hook: max 14 words, creates curiosity about #1, no numbers.
- Outro: one dry verdict sentence (max 14 words) + a question inviting viewers
  to comment (max 10 words).
- YouTube title: max 60 characters, no clickbait lies, no emojis, no hashtags.
- Description: 2 short sentences in plain English. Do not include the source;
  it is appended automatically.

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
    for m in NUM_RE.findall(ranking.title + " " + ranking.subtitle):
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
