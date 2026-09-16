"""Prompt construction for extraction v0.

The system prompt is frozen text (no timestamps, no per-document content)
so it is cacheable across documents; everything document-specific goes in
the user message. Field rules are lifted from ADR-001 §4, ADR-002 §2-§4 and
the FINDINGS §3 field notes — each rule here exists because a real
document broke the naive version of it.
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """\
You extract a structured incident record from an engineering postmortem.

Return exactly one JSON object matching the provided schema: a `record` and a
`confidence` object with one number in [0, 1] per record field. Report only
what the document supports. When the document is silent, use null (or an empty
list) — do not guess, and do not fill a field from general knowledge of the
company or the incident.

Field rules:

trigger / mechanism (two fields, not one):
- `trigger` is the initiating change or event that activated the failing path.
  It is null ONLY when the document identifies no initiating change (e.g. a
  latent race condition that surfaced on its own). If several changes could
  count, choose the change closest to the failure that was necessary to
  activate it — not simply the earliest event.
- `mechanism` is what actually failed, and is required. Exactly one primary
  mechanism; put additional mechanisms in contributing_factors.
- `mechanism.label` is one of the closed classes listed with their definitions
  in the schema; any other string is rejected. Pick the class whose definition
  matches what directly produced the failure, and use `other` only when none
  fits.
- `trigger.label` is a short snake_case category label for the KIND of change
  or event (its vocabulary is open; choose the most natural general label, not
  a document-specific phrase).

detection_method — the FIRST signal that caused someone responsible for the
system to recognise there was an incident worth investigating:
- monitoring: an automated system produced the signal (alerts, automated tests,
  health checks, synthetic probes, dashboards).
- customer_report: customers/users reported it before the org noticed.
- operator: the person performing the triggering operation noticed it going
  wrong themselves.
- internal_manual: another internal human noticed it through normal use or
  manual checking.
- ambiguous: the document describes several detection paths but does not
  establish which produced acknowledgment.
- unknown: the document gives no usable detection information.
A signal that existed but was dismissed and not acted on is NOT the detection;
use the first acknowledged signal.

Time anchors (change_at, impact_start, detected_at, mitigated_at, resolved_at):
- Each is null unless the document states it. `at` is ISO-8601; use date-only
  with precision "day" when no time is given. Copy the timezone string as
  written ("UTC", "PST"); null if none.
- If a timeline table and the prose disagree, prefer the timeline and set
  source_section accordingly. Never reconcile two conflicting times into a third.
- detected_at follows the same first-acknowledged-signal rule as detection_method.
- mitigated_at is when impact stopped for most users; resolved_at is when the
  org declared it over or fully recovered (including backfills).
- Do not compute durations. Only fill time_to_detect_text / time_to_mitigate_text
  with the author's own phrase if one exists.

contributing_factors: only factors the author states contributed. Do not derive
factors by inverting the remediation list. Record which section each came from.

mitigations vs remediations: mitigations are actions taken during the incident
to reduce impact (rollback, disable, throttle, failover). Remediations are
actions after the incident to prevent recurrence. Mark each done / planned /
proposed from tense and any dates.

Organisations: publisher_org is who published; affected_org is whose service
was down; vendor_org is a third party whose component failed, if named.

affected: list services as the document names them. Set all_services if the
document says the whole platform was affected. list_is_complete is true only
if the list is presented as exhaustive, false if the document says it is
partial, null if it does not say.

title: if the message provides a document title, copy it verbatim and set
title_source to "document_metadata". Otherwise write a short factual title and
set title_source to "synthesized".

confidence: for each record field, the probability that a careful human
labeler reading the same document would agree with your value. Use low values
when you had to infer, when the document contradicts itself, or when a null
might be wrong.
"""


def build_user_message(*, document_text: str, document_title: str | None, source_url: str) -> str:
    header = {"source_url": source_url, "document_title": document_title}
    return (
        "Document metadata:\n"
        f"{json.dumps(header, ensure_ascii=False)}\n\n"
        "Document text:\n"
        "<document>\n"
        f"{document_text}\n"
        "</document>\n\n"
        "Extract the incident record and per-field confidence."
    )


def build_retry_message(validation_errors: str) -> str:
    return (
        "Your previous output failed schema validation:\n"
        f"{validation_errors}\n\n"
        "Return the complete corrected JSON object (record and confidence). "
        "Fix only what the errors require; keep every other value the same."
    )


def initial_messages(user_message: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": user_message}]
