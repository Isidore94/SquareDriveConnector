from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

PARSER_VERSION = "1.0"
HEADER_REQUIRED = {"Club", "Index"}
SUMMARY_LABELS = {"average", "avg", "deviation", "standard deviation", "summary", "total"}


@dataclass
class ParsedShot:
    source_row: int
    source_index: str
    club_original: str
    club_normalized: str
    raw_values: dict[str, str]
    normalized: dict[str, object]
    fingerprint: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    original_timestamp_text: str | None
    session_time_iso: str | None
    place: str | None
    headers: list[str]
    shots: list[ParsedShot]
    malformed: list[dict[str, object]]
    warnings: list[str]


def _decode(data: bytes) -> str:
    # BOM-less UTF-16 decodes almost any even-length input, so only try it after a BOM.
    utf16 = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ()
    for encoding in ("utf-8-sig", *utf16, "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("CSV uses an unsupported text encoding")


def _number(value: str) -> float | None:
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _direction(value: str) -> float | None:
    """Square L/R notation: right is positive, left is negative."""
    text = value.strip().upper()
    match = re.fullmatch(r"([LR])\s*([+-]?\d+(?:\.\d+)?)", text)
    if match:
        magnitude = float(match.group(2))
        return abs(magnitude) if match.group(1) == "R" else -abs(magnitude)
    return _number(value)


CLUB_ALIASES = {
    "d": "Driver", "driver": "Driver", "3w": "3 Wood", "3 wood": "3 Wood",
    "5w": "5 Wood", "5 wood": "5 Wood", "pw": "Pitching Wedge", "p wedge": "Pitching Wedge",
    "sw": "Sand Wedge", "gw": "Gap Wedge", "aw": "Approach Wedge",
}


def normalize_club(value: str) -> str:
    compact = " ".join(value.strip().split()).lower()
    return CLUB_ALIASES.get(compact, value.strip() or "Unknown")


def _parse_metadata(rows: list[list[str]], timezone: str) -> tuple[str | None, str | None, str | None]:
    timestamp = place = None
    for row in rows:
        for i, cell in enumerate(row):
            if cell.strip().lower() == "dates" and i + 1 < len(row): timestamp = row[i + 1].strip() or None
            if cell.strip().lower() in {"place", "location"} and i + 1 < len(row): place = row[i + 1].strip() or None
    parsed = None
    if timestamp:
        cleaned = re.sub(r"\.(?=\s|$)", "", timestamp)
        for fmt in ("%H:%M %d %b %Y", "%H:%M %d %B %Y", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(cleaned, fmt).replace(tzinfo=ZoneInfo(timezone)).isoformat()
                break
            except ValueError:
                continue
    return timestamp, parsed, place


def parse_square_csv(data: bytes, timezone: str) -> ParseResult:
    text = _decode(data)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header_row = next((i for i, row in enumerate(rows) if HEADER_REQUIRED.issubset({x.strip() for x in row})), None)
    if header_row is None:
        raise ValueError("No Square shot-data header containing Club and Index was found")
    headers = [h.strip() for h in rows[header_row]]
    timestamp, parsed_time, place = _parse_metadata(rows[:header_row], timezone)
    malformed: list[dict[str, object]] = []
    warnings: list[str] = []
    if timestamp and not parsed_time: warnings.append("Session timestamp could not be parsed; original text preserved.")
    shots: list[ParsedShot] = []
    for row_number, row in enumerate(rows[header_row + 1:], start=header_row + 2):
        if not any(cell.strip() for cell in row): continue
        if row == headers or {x.strip() for x in row}.issuperset(HEADER_REQUIRED): continue
        if len(row) != len(headers):
            malformed.append({"source_row": row_number, "reason": "column_count", "raw": row})
            continue
        raw = dict(zip(headers, row))
        club = raw.get("Club", "").strip()
        if club.lower() in SUMMARY_LABELS or not club:
            if club: warnings.append(f"Summary row excluded at source row {row_number}: {club}")
            continue
        index = raw.get("Index", "").strip()
        if not index:
            malformed.append({"source_row": row_number, "reason": "missing_index", "raw": row})
            continue
        normalized: dict[str, object] = {}
        for key, value in raw.items():
            if key in {"Launch Direction", "Spin Axis", "Side Spin", "Offline(yd)", "Club Path", "Face Angle", "ImpactHorizontal"}:
                normalized[key] = _direction(value)
            else:
                normalized[key] = _number(value)
        shot_warnings: list[str] = []
        if _number(raw.get("ClubSpeed", "")) == 0 or _number(raw.get("SmashFactor", "")) == 0:
            shot_warnings.append("possible_placeholder_zero")
        content = "|".join([club, index] + [raw[h] for h in headers])
        fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
        shots.append(ParsedShot(row_number, index, club, normalize_club(club), raw, normalized, fingerprint, shot_warnings))
    if not shots and not malformed:
        raise ValueError("No individual Square shot rows were found")
    return ParseResult(timestamp, parsed_time, place, headers, shots, malformed, warnings)
