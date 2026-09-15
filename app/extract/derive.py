"""Values computed from a validated record rather than extracted.

FINDINGS §4.2: `time_to_detect` / `time_to_mitigate` as extracted intervals
could not represent "within minutes" or say which two anchors were
subtracted. So they are derived here from the typed anchors, and the
derivation names the pair it used. Absent anchors give None, not a coerced
number; day-precision anchors give None too (a difference of dates is not
a duration anyone should compare across orgs).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.extract.schema import IncidentRecord, TimeAnchor, parse_anchor

_ORDER = ("exact", "minute", "hour", "approximate")


def _seconds_between(start: TimeAnchor | None, end: TimeAnchor | None) -> dict[str, Any] | None:
    if start is None or end is None:
        return None
    if start.precision == "day" or end.precision == "day":
        return None
    a = parse_anchor(start.at)
    b = parse_anchor(end.at)
    if not isinstance(a, datetime) or not isinstance(b, datetime):
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        # One aware, one naive: not comparable without a timezone decision
        # this module refuses to make (FINDINGS §2.8).
        return None
    seconds = (b - a).total_seconds()
    coarser = max(start.precision, end.precision, key=_ORDER.index)
    return {"seconds": seconds, "precision": coarser}


def derive_durations(record: IncidentRecord) -> dict[str, Any]:
    """Return {"time_to_detect": {...}|None, "time_to_mitigate": {...}|None}.

    Each present value carries `seconds`, the `precision` of the coarser
    anchor, and the anchor pair (`from`, `to`) it was computed from, so a
    consumer can tell "24 h from change" from "0 s from impact" (Roblox).
    """
    ttd = _seconds_between(record.impact_start, record.detected_at)
    if ttd is not None:
        ttd |= {"from": "impact_start", "to": "detected_at"}
    ttm = _seconds_between(record.impact_start, record.mitigated_at)
    if ttm is not None:
        ttm |= {"from": "impact_start", "to": "mitigated_at"}
    return {"time_to_detect": ttd, "time_to_mitigate": ttm}
