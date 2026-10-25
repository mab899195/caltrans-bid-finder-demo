"""Electrical-work detection. District and estimate filtering happen on the page."""
import re

import config

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(k).replace(r"\ ", r"\s+")
        for k in sorted(config.ELECTRICAL_KEYWORDS, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)


def keyword_hits(text):
    """Distinct electrical keywords found in text, lowercased, in order found."""
    seen = []
    for m in _KEYWORD_RE.finditer(text or ""):
        k = re.sub(r"\s+", " ", m.group(1).lower())
        if k not in seen:
            seen.append(k)
    return seen


def electrical_items(bid_items):
    """Bid items whose code is in an electrical Standard Specifications section,
    each marked ``minor`` if it doesn't count toward flagging the project."""
    return [
        {**i, "minor": i["code"] in config.MINOR_ELECTRICAL_ITEMS}
        for i in bid_items
        if len(i["code"]) == 6 and i["code"][:2] in config.ELECTRICAL_ITEM_SECTIONS
    ]


def has_electrical_scope(keyword_hits, electrical_items):
    return bool(keyword_hits) or any(
        i["code"] not in config.MINOR_ELECTRICAL_ITEMS for i in electrical_items or []
    )
