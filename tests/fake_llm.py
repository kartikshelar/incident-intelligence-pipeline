"""Test doubles for app.extract.llm.LLMClient, plus a known-good output.

`FakeLLMClient` is scripted: each call pops the next item — a string
(returned as the model's text) or an exception (raised). It records every
call's messages so tests can assert what the model was shown on a retry.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.extract.llm import LLMResponse
from app.extract.schema import MAX_DESCRIPTION_CHARS


def valid_output() -> dict[str, Any]:
    """A schema-valid ExtractionOutput as a plain dict (Cloudflare-shaped)."""
    record = {
        "publisher_org": "Cloudflare",
        "affected_org": "Cloudflare",
        "vendor_org": None,
        "affected_org_kind": "company",
        "title": "Cloudflare outage on November 18, 2025",
        "title_source": "synthesized",
        "summary": "A database permissions change caused an oversized Bot Management "
        "feature file, which the core proxy could not load, causing HTTP 5xx errors.",
        "affected": {
            "services": ["Core CDN", "Bot Management", "Workers KV"],
            "regions": [],
            "all_services": False,
            "list_is_complete": True,
        },
        "trigger": {
            "label": "config_change",
            "description": "A database permissions change altered the feature-file query output.",
            "quote": "a change to one of our database systems' permissions",
        },
        "mechanism": {
            "label": "crash_on_bad_input",
            "description": "The proxy panicked when the feature file exceeded its size limit.",
            "quote": "the software panicked",
        },
        "contributing_factors": [
            {
                "text": "feature-generation query did not filter by database name",
                "source_section": "body",
            },
            {"text": "core proxy had a hard feature limit", "source_section": "body"},
        ],
        "detection_method": "monitoring",
        "detection_quote": "our first automated test detected the issue",
        "change_at": {
            "at": "2025-11-18T11:05:00",
            "precision": "minute",
            "timezone": "UTC",
            "quote": "11:05 change deployed",
            "source_section": "timeline",
        },
        "impact_start": {
            "at": "2025-11-18T11:28:00",
            "precision": "minute",
            "timezone": "UTC",
            "quote": "Impact starts 11:28",
            "source_section": "timeline",
        },
        "detected_at": {
            "at": "2025-11-18T11:31:00",
            "precision": "minute",
            "timezone": "UTC",
            "quote": "11:31 automated tests",
            "source_section": "timeline",
        },
        "mitigated_at": {
            "at": "2025-11-18T14:30:00",
            "precision": "minute",
            "timezone": "UTC",
            "quote": "14:30 core traffic flowing",
            "source_section": "timeline",
        },
        "resolved_at": {
            "at": "2025-11-18T17:06:00",
            "precision": "minute",
            "timezone": "UTC",
            "quote": "17:06 all services",
            "source_section": "timeline",
        },
        "time_to_detect_text": None,
        "time_to_mitigate_text": None,
        "blast_radius": {
            "qualitative": "Core network traffic failed for roughly three hours.",
            "quantitative": [],
        },
        "mitigations": [
            {
                "text": "Stopped generation and rolled back the feature file",
                "status": "done",
                "date": None,
            },
        ],
        "remediations": [
            {
                "text": "Harden ingestion of internal configuration files",
                "status": "planned",
                "date": None,
            },
        ],
    }
    confidence = {field: 0.9 for field in record}
    return {"record": record, "confidence": confidence}


def valid_output_json() -> str:
    return json.dumps(valid_output())


def invalid_output_json() -> str:
    """Schema-valid JSON syntax, invalid content: draft-era enum value."""
    bad = copy.deepcopy(valid_output())
    bad["record"]["detection_method"] = "automated"  # merged into monitoring, ADR-002
    return json.dumps(bad)


def overlong_description_output_json() -> str:
    """Otherwise valid, but `mechanism.description` is one sentence well over
    the character cap (v0.4: 400) — the paragraph-as-one-sentence shape the
    cap exists to reject."""
    bad = copy.deepcopy(valid_output())
    bad["record"]["mechanism"]["description"] = (
        "The Bot Management feature file, now containing duplicate rows and exceeding a "
        "hardcoded 200-feature limit, caused the FL2 proxy's Rust code to panic on an "
        "unhandled error when it tried to load the file, so every request through the "
        "core proxy returned an HTTP 5xx error until the file was rolled back, which took "
        "several hours because the file was regenerated every five minutes and each "
        "regeneration alternately produced a good and a bad file depending on which "
        "ClickHouse node served the query, so the symptoms fluctuated and were first "
        "mistaken for a large-scale DDoS attack against the network."
    )
    assert len(bad["record"]["mechanism"]["description"]) > MAX_DESCRIPTION_CHARS
    return json.dumps(bad)


class FakeLLMClient:
    provider = "fake"

    def __init__(self, script: list[str | BaseException], *, model: str = "fake-model") -> None:
        self.script = list(script)
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "schema": schema})
        if not self.script:
            raise AssertionError("FakeLLMClient called more times than scripted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return LLMResponse(
            text=item,
            model=self.model,
            stop_reason="end_turn",
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
