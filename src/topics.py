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
    note: str = ""        # optional verified context (never invented)


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
    extra: dict = field(default_factory=dict)


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


def bigmac_price(n, most_expensive=True):
    date, rows = _bigmac_latest()
    items = [Item(r["name"], float(r["dollar_price"]),
                  f"${float(r['dollar_price']):.2f}") for r in rows]
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
    )


# -------------------------------------------------------------- registry ----
TOPICS = {
    "bigmac-expensive": lambda n: bigmac_price(n, True),
    "bigmac-cheapest": lambda n: bigmac_price(n, False),
}


def pick(n, topic_id=None, exclude=()):
    if topic_id:
        return TOPICS[topic_id](n)
    choices = [t for t in TOPICS if t not in exclude] or list(TOPICS)
    return TOPICS[random.choice(choices)](n)
