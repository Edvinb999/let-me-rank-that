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
- Hook: max 8 words and NO digits (numbers take too long to say; the
  reference value is already on screen). #{n} must appear within ~2 seconds.
  Give the viewer a stake or a tease about #1. No "welcome", no "today".
  "Let me rank that" is optional; only use it if it sounds natural.
- Each item line: max 16 words, names the item (its name or one of its
  "also_called" names), never says the rank.
  Every item has several facts in "note" (separated by ";"). Use at most ONE
  fact per line and do NOT use the same kind of comparison in two lines in a
  row - rotate between them, or use none and just react. Variety is the point.
  No two lines may start with the same word.
- If a note says a figure is an estimate, say so naturally ("roughly",
  "an estimated", "about") and never present it as exact.
- No dashes (— or –); use commas or full stops. Write for the ear.
- The #1 line should land as the payoff.
- Outro: ONE short question (max 9 words) that invites a comment.
- YouTube title: max 60 characters, curiosity without lies, no emojis/hashtags.
- Description: 2 short plain sentences. No source (appended automatically).

Reply with ONLY the JSON object below: no preamble, no explanation, no
markdown fences. If a rule seems impossible, still return your best JSON.
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
    hook = data["hook"]
    if len(hook.split()) > 9:
        return "hook longer than 8 words"
    if re.search(r"\d", hook):
        return "hook contains a number"
    firsts = [ln.split()[0].lower().strip(",.") for ln in data["lines"] if ln.split()]
    if len(set(firsts)) < len(firsts):
        return "two lines start with the same word"
    allowed = _allowed_numbers(ranking)
    spoken = [data["hook"], data["outro"], *data["lines"]]
    for text in spoken + [data["yt_title"], data["description"]]:
        for m in NUM_RE.findall(text):
            if _norm(m) not in allowed:
                return f"unsupported number {m!r} in: {text}"
    # each line must mention its item (lines are #n..#1, items[0] is #1)
    generic = {"lake", "mount", "tower", "the", "center", "centre", "blue",
               "giant", "clam", "fish", "whale", "shark", "united"}
    for line, item in zip(data["lines"], reversed(ranking.items)):
        low = line.lower()
        names = [item.name] + list(item.aliases or [])
        tokens = [w for nm in names
                  for w in re.split(r"[\s/'’-]+", nm.lower())
                  if len(w) >= 3 and w not in generic]
        if tokens and not any(t in low for t in tokens):
            return f"line does not name {item.name!r}: {line}"
        if not tokens and item.name.lower() not in low:
            return f"line does not name {item.name!r}: {line}"
    return None


def _clean(text):
    """Speech-friendly: no dashes (they glue caption words together)."""
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse(text):
    """Take the first complete JSON object in the reply, ignoring any
    markdown fences or stray text around it."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return data


def write(ranking, cfg):
    n = len(ranking.items)
    payload = {
        "title": ranking.title,
        "subtitle": ranking.subtitle,
        "what_the_number_means": ranking.unit_hint,
        "reference": ranking.reference or None,
        "countdown": [
            {"rank": n - i, "name": it.name, "display": it.display,
             "note": it.note, "also_called": it.aliases or None}
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
        text = "".join(getattr(b, "text", "") for b in msg.content
                       if b.type == "text")
        try:
            data = _parse(text)
            problem = _check(data, ranking)
        except Exception as e:  # noqa: BLE001
            problem = f"invalid JSON ({e})"
        if not problem:
            for k in ("hook", "outro"):
                data[k] = _clean(data[k])
            data["lines"] = [_clean(x) for x in data["lines"]]
            return data
        print(f"[script] attempt {attempt} rejected: {problem}")
        if problem.startswith("invalid JSON"):
            kinds = [b.type for b in msg.content]
            print(f"   stop_reason={msg.stop_reason} blocks={kinds} "
                  f"reply starts: {text[:300]!r}")
        feedback = (f"\n\nYour previous reply was rejected: {problem}. "
                    "Fix it and follow the HARD RULES exactly.")
    raise RuntimeError("Could not get a valid script")
