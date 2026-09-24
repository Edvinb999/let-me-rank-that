"""Topic registry. Every number in a video comes from here, never from Claude.

A topic returns a Ranking: the top-N items (worst..best order is decided by the
renderer), each with a real value, plus the source line shown on screen and in
the description.
"""
import csv
import io
import random
from dataclasses import dataclass, field

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
    items = [Item(r["name"], float(r["dollar_price"]),
                  f"${float(r['dollar_price']):.2f}",
                  compare_note(float(r["dollar_price"]), ref_v,
                               "the United States"),
                  iso2(r["iso_a3"]))
             for r in rows if r["iso_a3"] != "USA"]
    items.sort(key=lambda i: i.value, reverse=most_expensive)
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


# -------------------------------------------------------------- registry ----
TOPICS = {
    "bigmac-expensive": lambda n: bigmac_price(n, True),
    "bigmac-cheapest": lambda n: bigmac_price(n, False),
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
