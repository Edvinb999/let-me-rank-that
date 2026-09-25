"""Topic registry. Every number in a video comes from here, never from Claude.

A topic returns a Ranking: the top-N items (worst..best order is decided by the
renderer), each with a real value, plus the source line shown on screen and in
the description.
"""
import csv
import io
import random
from dataclasses import dataclass, field
from pathlib import Path

import requests

BIGMAC_URL = ("https://raw.githubusercontent.com/TheEconomist/big-mac-data/"
              "master/output-data/big-mac-full-index.csv")


@dataclass
class Item:
    name: str
    value: float
    display: str          # how the number is shown and spoken, e.g. "$7.99"
    note: str = ""        # verified context computed from the data (never invented)
    code: str = ""        # ISO-2 country code -> flag, "" for non-countries
    aliases: list = field(default_factory=list)  # other accepted names


@dataclass
class Ranking:
    topic_id: str
    pillar: str           # money | animals | records
    title: str            # on-screen title, e.g. "Most expensive Big Mac"
    subtitle: str         # e.g. "Price in US dollars, July 2026"
    unit_hint: str        # tells the script writer what the number means
    items: list           # sorted, items[0] is #1
    source: str           # short on-screen credit
    source_long: str      # description credit incl. link
    reference: dict = field(default_factory=dict)  # {"name","value","display"}
    extra: dict = field(default_factory=dict)

    def spread(self):
        """0..1, how far apart the items are. Low = boring countdown."""
        vals = [abs(i.value) for i in self.items]
        return (max(vals) - min(vals)) / (max(vals) or 1)


# ---------------------------------------------------------------- Big Mac ----
_bigmac_cache = None


def _bigmac_latest():
    global _bigmac_cache
    if _bigmac_cache is None:
        r = requests.get(BIGMAC_URL, timeout=30)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        latest = max(row["date"] for row in rows)
        _bigmac_cache = (latest, [row for row in rows if row["date"] == latest])
    return _bigmac_cache


def _month_label(iso_date):
    import datetime as dt
    return dt.date.fromisoformat(iso_date).strftime("%B %Y")


_ISO = None


def iso2(iso3):
    global _ISO
    if _ISO is None:
        import json
        import zipfile
        from pathlib import Path
        z = Path(__file__).resolve().parent.parent / "assets/flags.zip"
        with zipfile.ZipFile(z) as zf:
            _ISO = json.loads(zf.read("iso3_to_iso2.json"))
    return _ISO.get(iso3, "")


def compare_note(value, ref_value, ref_name):
    """Verified comparison computed in code, e.g. '45% more than the United States'."""
    if not ref_value:
        return ""
    pct = round((value / ref_value - 1) * 100)
    if pct == 0:
        return f"about the same as {ref_name}"
    if value / ref_value >= 2:
        x = value / ref_value
        return f"{x:.1f} times the price in {ref_name}"
    word = "more" if pct > 0 else "cheaper"
    return f"{abs(pct)}% {word} than in {ref_name}"


def bigmac_price(n, most_expensive=True):
    date, rows = _bigmac_latest()
    us = next((r for r in rows if r["iso_a3"] == "USA"), None)
    ref_v = float(us["dollar_price"]) if us else 0
    priced = sorted((r for r in rows if r["iso_a3"] != "USA"),
                    key=lambda r: float(r["dollar_price"]),
                    reverse=most_expensive)
    top = priced[:n]
    # the opposite end of the table, for cross-comparisons
    other = sorted(priced, key=lambda r: float(r["dollar_price"]),
                   reverse=not most_expensive)[0]
    other_v = float(other["dollar_price"])
    items = []
    for idx, r in enumerate(top):
        v = float(r["dollar_price"])
        facts = [compare_note(v, ref_v, "the United States")]
        if idx + 1 < len(top):  # vs the item ranked just below it
            below = top[idx + 1]
            diff = abs(v - float(below["dollar_price"]))
            facts.append(f"${diff:.2f} {'more' if most_expensive else 'less'}"
                         f" than {below['name']}")
        ratio = max(v, other_v) / min(v, other_v)
        if most_expensive:
            facts.append(f"{ratio:.1f} times the price in {other['name']}")
        else:
            facts.append(f"one Big Mac in {other['name']} costs as much as "
                         f"{ratio:.1f} here")
        items.append(Item(r["name"], v, f"${v:.2f}", "; ".join(facts),
                          iso2(r["iso_a3"])))
    word = "Most expensive" if most_expensive else "Cheapest"
    tid = "bigmac-expensive" if most_expensive else "bigmac-cheapest"
    return Ranking(
        topic_id=tid, pillar="money",
        title=f"{word} Big Mac",
        subtitle=f"Price in US dollars · {_month_label(date)}",
        unit_hint=("price of one Big Mac burger in that country, converted to "
                   "US dollars at market exchange rates"),
        items=items[:n],
        source=f"Source: The Economist Big Mac Index ({_month_label(date)})",
        source_long=("Data: The Economist, Big Mac Index, "
                     f"{_month_label(date)} release — "
                     "https://github.com/TheEconomist/big-mac-data"),
        reference=({"name": "United States", "value": ref_v,
                    "display": f"${ref_v:.2f}", "code": "us"} if us else {}),
    )


# -------------------------------------------------------- curated facts ----
FACTS_FILE = Path(__file__).resolve().parent.parent / "data/facts.yaml"


def _fmt(v, decimals, unit):
    s = f"{v:,.{decimals}f}"
    return f"{s} {unit}".strip()


def curated(topic, n):
    """Build a Ranking from data/facts.yaml (values and sources are curated)."""
    unit, dec = topic.get("unit", ""), topic.get("decimals", 0)
    raw = sorted(topic["items"], key=lambda i: i["value"], reverse=True)[:n]
    last = raw[-1]
    items = []
    for idx, it in enumerate(raw):
        facts = []
        if it.get("note"):
            facts.append(it["note"])
        if it.get("disputed"):
            facts.append("this figure is an estimate; sources differ")
        if idx + 1 < len(raw):
            below = raw[idx + 1]
            gap = it["value"] - below["value"]
            if gap > 0 and not (it.get("disputed") or below.get("disputed")):
                gdisp = (it["value"] / below["value"] - 1) * 100
                if unit in ("km²",):
                    facts.append(f"{gdisp:.0f}% bigger than {below['name']}")
                else:
                    gdec = 1 if abs(gap - round(gap)) > 1e-9 else 0
                    facts.append(f"{_fmt(gap, gdec, unit)} "
                                 f"more than {below['name']}")
        if idx == 0 and last["value"] and it["value"] / last["value"] >= 1.15:
            facts.append(f"{it['value'] / last['value']:.1f} times "
                         f"{last['name']}")
        ref = topic.get("reference")
        if ref and ref.get("value") and it["value"] / ref["value"] >= 1.15:
            facts.append(f"{it['value'] / ref['value']:.1f} times "
                         f"{ref.get('spoken', ref['name'])}")
        items.append(Item(it["name"], float(it["value"]),
                          it.get("display") or _fmt(it["value"], dec, unit),
                          "; ".join(facts), it.get("code", ""),
                          list(it.get("aliases", []))))
    return Ranking(
        topic_id=topic["id"], pillar=topic["pillar"], title=topic["title"],
        subtitle=topic["subtitle"], unit_hint=topic["unit_hint"],
        items=items, source=topic["source"], source_long=topic["source_long"],
        reference=topic.get("reference") or {},
    )


def _load_curated():
    import yaml
    if not FACTS_FILE.exists():
        return {}
    data = yaml.safe_load(FACTS_FILE.read_text())
    return {t["id"]: (lambda n, t=t: curated(t, n)) for t in data["topics"]}


# -------------------------------------------------------------- registry ----
TOPICS = {
    "bigmac-expensive": lambda n: bigmac_price(n, True),
    "bigmac-cheapest": lambda n: bigmac_price(n, False),
    **_load_curated(),
}


MIN_SPREAD = 0.12  # skip rankings where everything is bunched together


def pick(n, topic_id=None, exclude=()):
    if topic_id:
        return TOPICS[topic_id](n)
    choices = [t for t in TOPICS if t not in exclude] or list(TOPICS)
    random.shuffle(choices)
    built = [TOPICS[t](n) for t in choices[:6]]
    good = [r for r in built if r.spread() >= MIN_SPREAD or r.reference]
    pool = good or built
    # prefer the most spread-out ranking, with a little randomness
    pool.sort(key=lambda r: r.spread() * random.uniform(0.8, 1.2), reverse=True)
    return pool[0]
