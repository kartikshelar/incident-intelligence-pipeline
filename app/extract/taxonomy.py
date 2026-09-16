"""Closed vocabularies used by the incident record.

detection_method — DECIDED by ADR-002 (docs/adr/002-detection-method.md):
  monitoring, customer_report, internal_manual, operator, ambiguous, unknown.
  `automated` was merged into `monitoring`; `operator` was split out of
  `internal_manual`; `ambiguous` (competing signals, ordering unknowable)
  is distinct from `unknown` (document says nothing usable).

mechanism — DECIDED by ADR-006 §3 (docs/adr/006-taxonomy-classes.md,
  DERIVE-01b): a closed enum of twelve classes including `other`. Each
  class's one-line definition from the ADR table is kept here verbatim
  (`MECHANISM_DEFINITIONS`) and rendered into the wire schema as the
  `mechanism.label` description, so the model reads the same definitions
  the labeler applies. A label outside the list fails validation and is
  retried with the error fed back. ADR-006 §8: every `other` is reviewed
  before the gold set is frozen, and more than 20% `other` means the list
  is too narrow.

trigger — STRUCTURE decided by ADR-001 (nullable initiating change/event),
  VALUES left open by ADR-006 §3: across the measured runs the label was
  stable on 7 of 10 documents with no confirmed semantic collisions, so it
  stays a snake_case string chosen by the model and is scored by concept
  match, not string equality. ADR-006 §5 (what is never a trigger) is a
  rule about the value, not a class list, and lives in the schema text.
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

# Open by ADR-006 §3 — see module docstring. Constrained to a snake_case
# label shape by the schema so free text can't leak in, but the *set* of
# labels is not fixed.
TriggerClass = str

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
    "other",
]
MECHANISM_CLASSES: tuple[str, ...] = get_args(MechanismClass)

# ADR-006 §3, the mechanism table, verbatim. Keyed in the enum's order;
# tests/test_extract_schema.py asserts the keys equal MECHANISM_CLASSES.
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
    "other": (
        "No existing mechanism class fits; every use of `other` must be reviewed "
        "before the gold set is frozen."
    ),
}


def mechanism_class_description() -> str:
    """The `mechanism.label` wire description: every class with its ADR-006
    definition, one per line, so the closed list and its meaning travel
    together in the cached system block."""
    lines = [f"- {name}: {definition}" for name, definition in MECHANISM_DEFINITIONS.items()]
    return (
        "Exactly one of the closed mechanism classes below (ADR-006 §3); any other "
        "value is rejected. Choose the class whose definition matches what "
        "directly produced the service failure; use `other` only when none fits.\n"
        + "\n".join(lines)
    )
