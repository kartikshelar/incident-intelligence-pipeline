"""Candidate passages for one field (M4 follow-up, task 2).

Reviewing a null or low-confidence field against a 24,000-character
document meant reading the document. This module picks three to five
passages a reviewer should look at first, chosen by keyword cues that fit
the field — deploy/config/rollout words for `trigger`, alert/paged/
noticed/reported for `detection_method`, clock times plus rollback/
restored words for `mitigated_at`, and so on. The full text stays on the
page below them.

WHY THE SELECTION NEVER LOOKS AT THE EXTRACTION. Corrections made on the
review page are gold-set input for M5 (ADR-010 §6): they become the ground
truth the extractor is scored against. A reviewer who is shown the
model's own justification — its cited quote, highlighted, or ranked
first — is no longer an independent judge; they are checking whether the
model's reasoning sounds right, and will tend to confirm it. Corrections
made that way would be biased toward the model's answer and would
silently corrupt the ground truth, and M5 would then measure agreement
with the extractor rather than correctness (PROJECT_BRIEF §4b names this
same circularity for model-generated labels). So `candidates()` takes the
field name and the document text and nothing else: no record, no quote,
no confidence. The model's quote can still land among the candidates, on
the same terms as any other passage, and the page never marks it. (The
one thing the page does say about a quote is when it is NOT in the
source — a check on the value's provenance, not a justification of it.)

WHY KEYWORDS. PROJECT_BRIEF §3: no embeddings, no vector store, no
retrieval model. The selection is a deterministic function of (field,
text): fixed cue lists, whole-word regex matches, passages scored by how
many distinct cues they contain, ties broken by position. The same
document and field always produce the same candidates, and a reviewer can
see which cues chose each one.

Passages: the text is split at sentence ends and line breaks, and
consecutive sentences are packed into windows of at most WINDOW_CHARS
characters, never across a blank line. Offsets are into the document text
so the reviewer can find a passage in the full source.
"""

from __future__ import annotations

import dataclasses
import re

WINDOW_CHARS = 400
MAX_CANDIDATES = 5
MIN_CANDIDATES = 3


@dataclasses.dataclass(frozen=True)
class Passage:
    start: int
    end: int
    text: str


@dataclasses.dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    text: str
    cues: tuple[str, ...]  # the cue names that matched; empty for a lead passage


@dataclasses.dataclass(frozen=True)
class _Cue:
    name: str
    pattern: re.Pattern[str]


def _words(*phrases: str) -> tuple[_Cue, ...]:
    """Whole-word, case-insensitive cues. A trailing `*` allows any word
    ending ("mitigat*" matches mitigated / mitigation / mitigating)."""
    out = []
    for phrase in phrases:
        literal = phrase.rstrip("*")
        # \b only where the phrase starts/ends with a word character; "%"
        # has no word boundary of its own.
        head = r"\b" if literal[0].isalnum() else ""
        if phrase.endswith("*"):
            tail = r"\w*"
        else:
            tail = r"\b" if literal[-1].isalnum() else ""
        out.append(_Cue(phrase, re.compile(rf"{head}{re.escape(literal)}{tail}", re.IGNORECASE)))
    return tuple(out)


_TIME = (
    _Cue("clock time", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")),
    _Cue(
        "date",
        re.compile(
            r"\b(?:\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
            r"[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?)\b",
            re.IGNORECASE,
        ),
    ),
    # Case-sensitive on purpose: "est" and "ist" are ordinary word endings.
    _Cue("timezone", re.compile(r"\b(?:UTC|GMT|PST|PDT|EST|EDT|CET|CEST|BST|IST|AEST)\b")),
)
_DURATION = (
    _Cue(
        "duration",
        re.compile(
            r"\b\d+\s*(?:minutes?|mins?|hours?|hrs?|seconds?|secs?|days?)\b|\bwithin\b",
            re.IGNORECASE,
        ),
    ),
)

_CHANGE = _words(
    "deploy*", "rollout", "rolled out", "roll out", "change*", "config*", "permission*",
    "migration*", "upgrade*", "update*", "maintenance", "enabled", "flag*", "release*",
    "pushed", "merged", "command", "ran", "executed", "failover", "promoted", "restart*",
    "introduced", "triggered", "caused by", "began", "started",
)
_FAILURE = _words(
    "panic*", "crash*", "fail*", "exhaust*", "exceed*", "limit*", "timeout*", "timed out",
    "overload*", "retr*", "race", "lock*", "deadlock*", "null", "nil", "out of memory", "oom",
    "bug*", "error*", "5xx", "500", "502", "503", "504", "latency", "unavailable", "corrupt*",
    "deleted", "dropped", "loop*", "saturat*", "queue*", "backlog", "cascad*", "thundering",
)
_FACTORS = _words(
    "contribut*", "factor*", "because", "due to", "lack of", "lacked", "missing", "no alert*",
    "did not", "didn't", "insufficient", "unaware", "gap*", "assumed", "assumption*",
    "root cause*", "underlying", "compounded", "made worse", "exacerbat*", "should have",
    "unfortunately", "however",
)
_DETECTION = _words(
    "alert*", "alarm*", "paged", "page", "pager*", "on-call", "oncall", "monitor*",
    "dashboard*", "noticed", "detect*", "report*", "customer*", "user*", "support", "ticket*",
    "tweet*", "twitter", "social media", "status page", "escalat*", "identified", "flagged",
    "observed", "aware", "investigat*", "declared", "opened", "health check*", "synthetic*",
    "probe*",
)
_IMPACT = _words(
    "impact*", "affect*", "began", "started", "first", "users", "customers", "errors",
    "degrad*", "outage", "down", "unavailable", "unable", "failing", "elevated",
)
_MITIGATION = _words(
    "mitigat*", "rollback", "rolled back", "roll back", "revert*", "disabled", "disable",
    "restored", "recover*", "failover", "failed over", "scaled", "throttl*", "restarted",
    "workaround", "returned to normal", "back to normal", "stabili*", "resolved", "fix*",
    "traffic",
)
_RESOLUTION = _words(
    "resolved", "resolution", "fully recovered", "fully restored", "all services", "restored",
    "closed", "complete*", "back to normal", "declared", "ended", "concluded", "over",
    "backfill*",
)
_REMEDIATION = _words(
    "remediat*", "action item*", "follow-up*", "follow up*", "going forward", "we will",
    "we are", "we plan", "we have", "prevent*", "future", "improve*", "implement*", "add*",
    "next steps", "lessons", "learned", "audit*", "harden*", "ensure",
)
_RADIUS = _words(
    "%", "percent*", "requests", "users", "customers", "region*", "affected", "impact*",
    "error rate", "of traffic", "all", "global*", "worldwide", "thousand*", "million*",
    "hundreds", "approximately", "roughly", "about",
)
_SCOPE = _words(
    "service*", "product*", "region*", "zone*", "affected", "impacted", "datacenter*",
    "data center*", "customers using", "api", "dashboard", "availability zone*", "cluster*",
    "endpoint*",
)
_ORG = _words(
    "company", "provider", "vendor*", "upstream", "third-party", "third party", "partner*",
    "open source", "open-source", "project", "maintainer*", "community", "inc", "llc", "ltd",
    "cloud provider", "aws", "azure", "gcp", "google cloud", "hosted", "customer of",
)
_TITLE = _words(
    "postmortem", "post-mortem", "post mortem", "incident", "outage", "report", "rca",
    "root cause analysis", "incident review", "summary", "retrospective",
)
_SUMMARY = _words(
    "summary", "overview", "tl;dr", "tldr", "in short", "what happened", "impact",
    "incident", "outage", "root cause", "background",
)

# One cue list per top-level record field. tests/test_review_candidates.py
# asserts the keys equal IncidentRecord's fields.
FIELD_CUES: dict[str, tuple[_Cue, ...]] = {
    "publisher_org": _ORG,
    "affected_org": _ORG,
    "vendor_org": _ORG,
    "affected_org_kind": _ORG,
    "title": _TITLE,
    "title_source": _TITLE,
    "summary": _SUMMARY,
    "affected": _SCOPE,
    "trigger": _CHANGE,
    "mechanism": _FAILURE,
    "contributing_factors": _FACTORS,
    "detection_method": _DETECTION,
    "detection_quote": _DETECTION,
    "change_at": _CHANGE + _TIME,
    "impact_start": _IMPACT + _TIME,
    "detected_at": _DETECTION + _TIME,
    "mitigated_at": _MITIGATION + _TIME,
    "resolved_at": _RESOLUTION + _TIME,
    "time_to_detect_text": _DETECTION + _DURATION,
    "time_to_mitigate_text": _MITIGATION + _DURATION,
    "blast_radius": _RADIUS,
    "mitigations": _MITIGATION,
    "remediations": _REMEDIATION,
}

# A sentence ends at . ! or ? (optionally followed by a closing quote or
# bracket) then whitespace, or at a line break.
_BOUNDARY = re.compile(r"(?P<punct>[.!?]+[\"')\]]*)\s+|\n")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def _sentences(document: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0

    def add(end: int) -> None:
        text = document[start:end]
        lead = len(text) - len(text.lstrip())
        trail = len(text) - len(text.rstrip())
        if end - trail > start + lead:
            spans.append((start + lead, end - trail))

    for match in _BOUNDARY.finditer(document):
        add(match.end("punct") if match.group("punct") else match.start())
        start = match.end()
    add(len(document))
    return spans


def passages(document: str, *, window: int = WINDOW_CHARS) -> list[Passage]:
    """Consecutive sentences packed into windows of at most `window`
    characters, never across a blank line. A single sentence longer than
    the window is a passage on its own."""
    out: list[Passage] = []
    current: tuple[int, int] | None = None
    for start, end in _sentences(document):
        if current is not None:
            gap = document[current[1] : start]
            if end - current[0] <= window and not _PARAGRAPH_BREAK.search(gap):
                current = (current[0], end)
                continue
            out.append(Passage(current[0], current[1], document[current[0] : current[1]]))
        current = (start, end)
    if current is not None:
        out.append(Passage(current[0], current[1], document[current[0] : current[1]]))
    return out


def candidates(field: str, document: str) -> list[Candidate]:
    """Three to five passages for `field`, in document order.

    Passages are scored by the number of distinct cues that match, then by
    total matches, then by position (earlier first); the top MAX_CANDIDATES
    with at least one match are taken. If fewer than MIN_CANDIDATES score,
    the earliest unscored passages fill the list — the lead of a postmortem
    is usually its summary — with no cues recorded. Deterministic: the
    same (field, document) always gives the same result. Takes no record
    and no quote, by design (module docstring)."""
    cues = FIELD_CUES[field]
    all_passages = passages(document)
    scored: list[tuple[int, int, int, int]] = []  # (distinct, hits, -start, index)
    for index, passage in enumerate(all_passages):
        matched = [len(cue.pattern.findall(passage.text)) for cue in cues]
        distinct = sum(1 for n in matched if n)
        if distinct:
            scored.append((distinct, sum(matched), -passage.start, index))
    scored.sort(reverse=True)
    chosen = {index for _, _, _, index in scored[:MAX_CANDIDATES]}
    for index in range(len(all_passages)):
        if len(chosen) >= MIN_CANDIDATES:
            break
        chosen.add(index)

    out: list[Candidate] = []
    for index in sorted(chosen):
        passage = all_passages[index]
        names = tuple(cue.name for cue in cues if cue.pattern.search(passage.text))
        out.append(Candidate(passage.start, passage.end, passage.text, names))
    return out
