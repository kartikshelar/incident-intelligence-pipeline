"""Closed vocabularies used by the incident record.

detection_method — DECIDED by ADR-002 (docs/adr/002-detection-method.md):
  monitoring, customer_report, internal_manual, operator, ambiguous, unknown.
  `automated` was merged into `monitoring`; `operator` was split out of
  `internal_manual`; `ambiguous` (competing signals, ordering unknowable)
  is distinct from `unknown` (document says nothing usable).

mechanism — DECIDED by ADR-006 §3, REVISED by ADR-008 §3 (docs/adr/
  008-taxonomy-revision.md): a closed enum of sixteen classes including
  `other`. ADR-006's twelve survive unchanged; ADR-008 adds
  `software_defect` (restored — ADR-006 §4 removed it, which ADR-008 §4
  says was too aggressive), `security_compromise`, `hardware_data_loss`,
  and `consistency_anomaly`. Each class's one-line definition is kept here
  verbatim (`MECHANISM_DEFINITIONS`) and rendered into the wire schema as
  the `mechanism.label` description, so the model reads the same
  definitions the labeler applies. A label outside the list fails
  validation and is retried with the error fed back. ADR-008 §5: no more
  than 20% of a HELD-OUT set may land on `other`, or the taxonomy has
  again failed to generalize.

trigger — STRUCTURE decided by ADR-001 (nullable initiating change/event).
  VALUES: ADR-006 §3 left them open; ADR-008 §3 closes them into a ten-
  class enum (`TRIGGER_DEFINITIONS`), derived by clustering the vocabulary
  observed across the 30-document corpus (`internal_config_change` /
  `config_change` was a confirmed same-concept collision — ADR-006's own
  condition for revisiting). Rendered into the wire schema the same way as
  mechanism. ADR-006 §5's rule (an anomalous condition is never itself the
  trigger) is now structural as well as textual: `traffic_spike`,
  `operational_delay` and `race_condition` are not in the enum at all.

Both class-list descriptions are built by the same function
(`_enum_description`) so where that text is placed in the rendered wire
schema (today: inline in the `label` field description) can be changed in
one place later without touching either taxonomy's data.
"""

from typing import Literal, get_args

DetectionMethod = Literal[
    "monitoring",
    "customer_report",
    "internal_manual",
    "operator",
    "ambiguous",
    "unknown",
]
DETECTION_METHODS: tuple[str, ...] = get_args(DetectionMethod)

TriggerClass = Literal[
    "config_change",
    "code_deploy",
    "manual_command",
    "os_auto_update",
    "infrastructure_maintenance",
    "content_update",
    "feature_rollout",
    "database_failover",
    "external_service_degradation",
    "account_compromise",
]
TRIGGER_CLASSES: tuple[str, ...] = get_args(TriggerClass)

MechanismClass = Literal[
    "accidental_data_deletion",
    "cascading_overload",
    "limit_violation",
    "lock_contention",
    "null_pointer_failure",
    "out_of_bounds_read",
    "race_condition",
    "resource_exhaustion",
    "route_deletion",
    "unsafe_failover",
    "workload_misrouting",
    "software_defect",
    "security_compromise",
    "hardware_data_loss",
    "consistency_anomaly",
    "other",
]
MECHANISM_CLASSES: tuple[str, ...] = get_args(MechanismClass)

# ADR-006 §3 (survivors) + ADR-008 §3 (additions), verbatim. Keyed in the
# enum's order; tests/test_extract_schema.py asserts the keys equal
# MECHANISM_CLASSES.
MECHANISM_DEFINITIONS: dict[str, str] = {
    "accidental_data_deletion": (
        "Production data is unintentionally deleted by an operator action or command."
    ),
    "cascading_overload": (
        "Interacting failures increase load or reduce effective capacity until the "
        "system enters a self-reinforcing overload state."
    ),
    "limit_violation": (
        "An input or state exceeds an enforced implementation limit, causing the "
        "consuming component to fail."
    ),
    "lock_contention": (
        "Concurrent operations contend for a shared lock or synchronization "
        "primitive, blocking forward progress."
    ),
    "null_pointer_failure": (
        "Missing or null data reaches a code path that does not safely handle the "
        "null value, causing execution failure."
    ),
    "out_of_bounds_read": (
        "Code attempts to read beyond the bounds of an available buffer, array, or input structure."
    ),
    "race_condition": (
        "Concurrent operations interact in a timing-dependent way that produces an "
        "invalid or inconsistent system state."
    ),
    "resource_exhaustion": (
        "A finite computational or infrastructure resource is depleted, preventing "
        "the affected component from continuing normal operation."
    ),
    "route_deletion": (
        "Required network routes are removed, breaking connectivity between affected "
        "components or nodes."
    ),
    "unsafe_failover": (
        "Failover moves service state or responsibility to a topology that the "
        "dependent system cannot safely support."
    ),
    "workload_misrouting": (
        "Work is incorrectly directed to an execution environment or destination "
        "other than the intended one."
    ),
    # ADR-008 §3: restored as a general class. Not a replacement for the
    # narrower software classes above (limit_violation, null_pointer_failure,
    # out_of_bounds_read, race_condition) — use one of those when the
    # evidence supports it; software_defect is the fallback only when a
    # software defect is identifiable but no narrower class fits.
    "software_defect": (
        "A defect in software logic or implementation causes failure and no "
        "existing narrower software-failure class adequately describes the "
        "mechanism. Use a narrower class (limit_violation, null_pointer_failure, "
        "out_of_bounds_read, race_condition, etc.) whenever the evidence supports "
        "it; software_defect is the fallback only when none of them fit."
    ),
    "security_compromise": (
        "Unauthorized or malicious activity compromises a system or security "
        "boundary and directly produces the incident's failure."
    ),
    "hardware_data_loss": (
        "Failure of hardware or firmware causes stored or in-flight data to be "
        "lost or irrecoverably corrupted."
    ),
    "consistency_anomaly": (
        "Components observe or maintain mutually inconsistent state, causing "
        "incorrect operation or preventing safe progress."
    ),
    "other": (
        "No existing mechanism class fits; every use of `other` must be reviewed "
        "before the gold set is frozen."
    ),
}

# ADR-008 §3, the trigger table, verbatim. Keyed in the enum's order;
# tests/test_extract_schema.py asserts the keys equal TRIGGER_CLASSES.
# traffic_spike, operational_delay and race_condition are deliberately
# absent: ADR-006 §5 holds that an anomalous condition is never itself the
# trigger (traffic_spike, operational_delay) and race_condition names a
# failure mechanism, not an initiating change or event — see the
# `trigger` field description in app/extract/schema.py for the rule as
# stated to the model.
TRIGGER_DEFINITIONS: dict[str, str] = {
    "config_change": (
        "A change to system configuration, permissions, settings, policies, or "
        "other operational configuration data initiates the incident."
    ),
    "code_deploy": (
        "A new or changed software build or software version is deployed into an "
        "environment and initiates the incident."
    ),
    "manual_command": (
        "An operator manually executes a command or operational procedure that "
        "initiates the incident."
    ),
    "os_auto_update": ("An automatically applied operating-system update initiates the incident."),
    "infrastructure_maintenance": (
        "Maintenance or replacement work on compute, network, storage, or other "
        "infrastructure initiates the incident."
    ),
    "content_update": (
        "New or changed non-code content or data is distributed to a running "
        "system and initiates the incident."
    ),
    "feature_rollout": (
        "A feature, capability, or feature-controlled behavior is newly enabled "
        "or expanded and initiates the incident."
    ),
    "database_failover": (
        "A database failover, promotion, or equivalent role transition initiates "
        "the incident."
    ),
    "external_service_degradation": (
        "Degradation or failure of an external dependency initiates the incident."
    ),
    "account_compromise": (
        "Unauthorized compromise of an account or security principal initiates "
        "the incident."
    ),
}


def _enum_description(*, intro: str, definitions: dict[str, str]) -> str:
    """Render a closed class list as wire-schema description text: an intro
    sentence followed by one `- class: definition` line per class, in the
    enum's own order. The one function both mechanism_class_description and
    trigger_class_description call, so where this text is placed relative to
    the rest of the schema is a change to this function alone."""
    lines = [f"- {name}: {definition}" for name, definition in definitions.items()]
    return intro + "\n" + "\n".join(lines)


def mechanism_class_description() -> str:
    """The `mechanism.label` wire description: every class with its
    ADR-006/ADR-008 definition, one per line, so the closed list and its
    meaning travel together in the cached system block."""
    return _enum_description(
        intro=(
            "Exactly one of the closed mechanism classes below (ADR-006 §3, "
            "ADR-008 §3); any other value is rejected. Choose the class whose "
            "definition matches what directly produced the service failure; use "
            "`other` only when none fits."
        ),
        definitions=MECHANISM_DEFINITIONS,
    )


def trigger_class_description() -> str:
    """The `trigger.label` wire description: every class with its ADR-008
    definition, one per line. `trigger` itself stays nullable (ADR-001); this
    describes the label an extraction gives it when it is not null."""
    return _enum_description(
        intro=(
            "Exactly one of the closed trigger classes below (ADR-008 §3); any "
            "other value is rejected. Choose the class that identifies the KIND "
            "of initiating change or external event."
        ),
        definitions=TRIGGER_DEFINITIONS,
    )
