"""Closed vocabularies used by the incident record.

detection_method — DECIDED by ADR-002 (docs/adr/002-detection-method.md):
  monitoring, customer_report, internal_manual, operator, ambiguous, unknown.
  `automated` was merged into `monitoring`; `operator` was split out of
  `internal_manual`; `ambiguous` (competing signals, ordering unknowable)
  is distinct from `unknown` (document says nothing usable).

trigger / mechanism classes — STRUCTURE decided by ADR-001, VALUES NOT YET
DECIDED. ADR-001 §1: "the actual class lists and enum values for these
fields are deferred to 01b", and there is no ADR 01b (or 004) in docs/adr/
as of M3. PROJECT_BRIEF §10 forbids deciding a [DERIVE] item in code, so
`TriggerClass` and `MechanismClass` are open strings here: the model is
asked for a short snake_case label and the label is stored verbatim. When
01b lands, replace the two aliases below with `Literal[...]` and the schema,
prompt, validation, and retry-with-feedback path all pick it up — nothing
else needs to change. Do NOT introduce a class list here without the ADR.
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

# Open pending ADR 01b — see module docstring. Constrained to a snake_case
# label shape by the schema so free text can't leak in, but the *set* of
# labels is not fixed.
TriggerClass = str
MechanismClass = str
