"""Bid results: the Bid Summary page (list of opened projects) and the Bid
Summary PDF Caltrans publishes for each one.

From each PDF we keep: the engineer's estimate, every bidder's rank / name /
total, the low bidder's electrical items with prices, and which subcontractors
each bidder listed for electrical items. The PDF text comes out of pypdf with
some fields glued together, hence the careful regexes below.
"""
import io
import re
from datetime import datetime

from pypdf import PdfReader

import config
from scraper.parse import ParseError, widget_data

_MONEY = r"[\d,]+\.\d{2}"

# Bump when parsing improves: results that failed under an older version are retried.
PARSER_VERSION = 2


class ScannedPdf(ParseError):
    """The summary pages are scanned images with no text layer."""


def _money(text):
    return float(text.replace(",", "").replace("$", ""))


def parse_summary_list(page_json):
    """Final bid summaries on the cc_bid_summary page: one entry per project."""
    data = widget_data(page_json, "cc_bid_summary_result")
    out = {}
    for date, entries in (data.get("projects") or {}).items():
        for e in entries:
            contract = (e.get("project") or "").strip()
            if contract and e.get("sys_id"):
                out[contract] = {"contract": contract, "bid_open": e.get("bidOpenDate") or date,
                                 "sys_id": e["sys_id"]}
    return sorted(out.values(), key=lambda e: (e["bid_open"], e["contract"]))


def parse_summary_pdf(pdf_bytes):
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf_bytes)).pages]
    if not pages:
        raise ParseError("empty PDF")
    first = pages[0]
    if not first.strip():
        raise ScannedPdf("scanned PDF (no text on page 1)")

    m = re.search(r"Contract Number: (\d\d-\w{6})", first)
    if not m:
        raise ParseError("no contract number on page 1; not a Bid Summary?")
    result = {"contract": m.group(1)}
    m = re.search(r"Bid Opening Date: (\d\d/\d\d/\d{4})", first)
    result["bid_open"] = datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat() if m else None
    m = re.search(r"^Location: (.+)$", first, re.M)
    loc = m.group(1).strip() if m else ""
    result["county_route_pm"] = loc.split("-", 1)[1] if re.match(r"\d\d-", loc) else loc
    result["description"] = _description(first)

    # "Number of Bidders: Engineers Est:" labels, values on a later line: "6 $4,822,132.36 -43.27%"
    m = re.search(rf"^(\d+) \$({_MONEY}) -?[\d.]+%", first, re.M)
    result["engineers_estimate"] = _money(m.group(2)) if m else None

    bidders, bidder_ids = [], {}
    item_pages = []
    for text in pages:
        if "Contract Proposal of Low Bidder" in text:
            item_pages.append(text)
            continue
        if item_pages:
            break  # past the low bidder's proposal; the rest is other bidders / subs
        for rank, amount, vid, name in re.findall(rf"^(\d+) \$({_MONEY}) (VC\w+) (.+)$", text, re.M):
            bidders.append({"rank": int(rank), "amount": _money(amount), "name": name.strip()})
            bidder_ids[vid] = int(rank)
        # A+B (cost + time) contracts: rank by the A+B total; "amount" stays the cost bid,
        # which is what the engineer's estimate covers.
        for rank, total, cost, vid, name in re.findall(
                rf"^(\d+)\nTotal:\nCost:\nTime:\n\$({_MONEY})\n\$({_MONEY}) (VC\w+) (.+)$", text, re.M):
            bidders.append({"rank": int(rank), "amount": _money(cost), "name": name.strip(),
                            "a_plus_b_total": _money(total)})
            bidder_ids[vid] = int(rank)
    if not bidders:
        raise ParseError("no bidders found")
    bidders.sort(key=lambda b: b["rank"])
    result["bidders"] = bidders

    # Full names (page 1 wraps long names); the proposal header has the low bidder's.
    m = re.search(r"Contract Proposal of Low Bidder\n(.+)", item_pages[0]) if item_pages else None
    if m:
        bidders[0]["name"] = m.group(1).strip()

    items = _low_bidder_items(item_pages)
    elec = [i for i in items if i["code"][:2] in config.ELECTRICAL_ITEM_SECTIONS]
    result["item_count"] = len(items)
    result["electrical_items"] = elec
    result["electrical_total"] = round(sum(i["amount"] for i in elec), 2)

    by_no = {i["no"]: i for i in items}
    sub_pages = [t for t in pages if "LIST OF SUBCONTRACTORS" in t]
    result["electrical_subs"] = _electrical_subs(sub_pages, by_no, bidder_ids, bidders)
    return result


def _description(first_page):
    lines = first_page.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Number of Items:")) + 1
    except StopIteration:
        return ""
    block = []
    for l in lines[start:]:
        if l.startswith(("Federal Aid", "Proposals Issued:")):
            break
        block.append(l.strip())
    # The location text is upper case; the work description has lower case letters.
    desc = [l for l in block if re.search(r"[a-z]", l)]
    return " ".join(desc) if desc else (block[-1] if block else "")


_ITEM_START = re.compile(r"^(" + _MONEY + r")(\d{4}) (\d{6}) (.*)$")
_NOISE = re.compile(r"^(Page \d+|\d\d/\d\d/\d{4}Contract Number|Bid Opening Date|Contract Proposal|"
                    r"AmountEstimated)")
# The proposal ends with the bid total(s): "$2,735,593.70Bid Amount:" or, for A+B
# contracts, "$cost" / "$total" / "Cost:" / "Time:" lines.
_END = re.compile(r"^(\$[\d,]+\.\d{2}|Cost:|Time:|Bid Amount:)")


def _low_bidder_items(item_pages):
    """Rows look like '<unit price><item no> <code> <description> <unit> <qty> <amount>'
    (price and item number are glued), or '... LS <amount>LUMP SUM'. Long
    descriptions wrap onto following lines."""
    rows, current = [], None
    for text in item_pages:
        lines = text.splitlines()
        # Skip the header and the bidder name line under it.
        for i, line in enumerate(lines):
            m = _ITEM_START.match(line)
            if m:
                if current:
                    rows.append(current)
                current = [m.group(2), m.group(3), m.group(4)]
            elif _END.match(line):
                if current:
                    rows.append(current)
                current = None
            elif current and not _NOISE.match(line) and not (i > 0 and lines[i - 1].startswith("Contract Proposal")):
                current[2] += " " + line.strip()
    if current:
        rows.append(current)

    items = []
    for no, code, rest in rows:
        m = (re.match(rf"^(?P<desc>.+) (?P<unit>\S+) (?P<amount>{_MONEY})LUMP SUM$", rest)
             or re.match(rf"^(?P<desc>.+) (?P<unit>\S+) (?P<qty>[\d,]+\.\d+) (?P<amount>{_MONEY})F?$", rest))
        if not m:
            continue
        g = m.groupdict()
        items.append({
            "no": int(no), "code": code, "description": g["desc"].strip(),
            "unit": g["unit"], "quantity": g.get("qty") or "1", "amount": _money(g["amount"]),
        })
    return items


def _item_numbers(work):
    work = re.sub(r"\([\d.]+%\)", "", work)
    nums = []
    for a, b in re.findall(r"(\d+)(?:\s*-\s*(\d+))?", work):
        nums.extend(range(int(a), int(b) + 1) if b else [int(a)])
    return nums


def _electrical_subs(sub_pages, items_by_no, bidder_ids, bidders):
    """Subcontractors listed for electrical items, per bidder (best effort:
    only rows naming item numbers, like 'ACME ELECTRIC ITEM 33 (100%)VC123')."""
    names = {b["rank"]: b["name"] for b in bidders}
    found = {}
    for text in sub_pages:
        for sub, work, vid in re.findall(r"^(.+?) ((?:BID )?ITEMS? [\d ,&\-]+.*?)(VC\w+)$", text, re.M):
            rank = bidder_ids.get(vid)
            codes = [items_by_no[n]["code"] for n in _item_numbers(re.sub(r"(BID )?ITEMS?", "", work))
                     if n in items_by_no and items_by_no[n]["code"][:2] in config.ELECTRICAL_ITEM_SECTIONS]
            if rank is None or not codes:
                continue
            key = (rank, sub.strip())
            found.setdefault(key, set()).update(codes)
    return [{"bidder_rank": rank, "bidder": names.get(rank, ""), "sub": sub, "items": sorted(codes)}
            for (rank, sub), codes in sorted(found.items())]
