# Label enums — reference sheet for gold-set labeling

Used with [gold_set_template.json](gold_set_template.json) per
[M5_PROTOCOL.md §3](M5_PROTOCOL.md). Do not add, remove, or reinterpret
any class while labeling is in progress (§5).

Source of truth: [app/extract/taxonomy.py](../app/extract/taxonomy.py)
(`MECHANISM_DEFINITIONS`, `TRIGGER_DEFINITIONS`, `DetectionMethod`) — the
enums extraction is actually validated against, and the same text the
model is given. Definitions below are copied verbatim from there. ADR 008
§3's own prose table only lists the classes it added or discusses; the
code comment at the top of `taxonomy.py` confirms the full mechanism
enum is ADR 006's twelve classes retained unchanged plus ADR 008's four
additions (sixteen total, including `other`).

---

## `trigger_label` — ADR 008 §3

Closed, ten-class enum. What changed or happened to **initiate** the
failure sequence — not the condition it produced. `traffic_spike`,
`operational_delay`, and `race_condition` are deliberately **not** in
this enum: the first two are anomalous conditions, not triggers (ADR 006
§5), and `race_condition` names a failure mechanism, not an initiating
change or event. If no identifiable initiating change or external event
is documented, the label is `null` — never force a guess
(M5_PROTOCOL.md §3).

| Class | One-line definition |
| --- | --- |
| `config_change` | A change to system configuration, permissions, settings, policies, or other operational configuration data initiates the incident. |
| `code_deploy` | A new or changed software build or software version is deployed into an environment and initiates the incident. |
| `manual_command` | An operator manually executes a command or operational procedure that initiates the incident. |
| `os_auto_update` | An automatically applied operating-system update initiates the incident. |
| `infrastructure_maintenance` | Maintenance or replacement work on compute, network, storage, or other infrastructure initiates the incident. |
| `content_update` | New or changed non-code content or data is distributed to a running system and initiates the incident. |
| `feature_rollout` | A feature, capability, or feature-controlled behavior is newly enabled or expanded and initiates the incident. |
| `database_failover` | A database failover, promotion, or equivalent role transition initiates the incident. |
| `external_service_degradation` | Degradation or failure of an external dependency initiates the incident. |
| `account_compromise` | Unauthorized compromise of an account or security principal initiates the incident. |
| `null` | No identifiable initiating change or external event is documented. |

---

## `mechanism_label` — ADR 006 §3 (survivors) + ADR 008 §3 (additions)

Closed, sixteen-class enum (including `other`). The technical process
through which the trigger produced failure. `software_defect` is
general-purpose: select a narrower class (`limit_violation`,
`null_pointer_failure`, `out_of_bounds_read`, `race_condition`, or
another specific class) whenever the evidence supports it;
`software_defect` is the fallback only when a software defect is
identifiable but no narrower class fits. `other` is permitted only when
no existing class fits at all, and every `other` use gets reviewed
before the gold set is frozen (ADR 008 §3).

| Class | One-line definition |
| --- | --- |
| `accidental_data_deletion` | Production data is unintentionally deleted by an operator action or command. |
| `cascading_overload` | Interacting failures increase load or reduce effective capacity until the system enters a self-reinforcing overload state. |
| `limit_violation` | An input or state exceeds an enforced implementation limit, causing the consuming component to fail. |
| `lock_contention` | Concurrent operations contend for a shared lock or synchronization primitive, blocking forward progress. |
| `null_pointer_failure` | Missing or null data reaches a code path that does not safely handle the null value, causing execution failure. |
| `out_of_bounds_read` | Code attempts to read beyond the bounds of an available buffer, array, or input structure. |
| `race_condition` | Concurrent operations interact in a timing-dependent way that produces an invalid or inconsistent system state. |
| `resource_exhaustion` | A finite computational or infrastructure resource is depleted, preventing the affected component from continuing normal operation. |
| `route_deletion` | Required network routes are removed, breaking connectivity between affected components or nodes. |
| `unsafe_failover` | Failover moves service state or responsibility to a topology that the dependent system cannot safely support. |
| `workload_misrouting` | Work is incorrectly directed to an execution environment or destination other than the intended one. |
| `software_defect` | A defect in software logic or implementation causes failure and no existing narrower software-failure class adequately describes the mechanism. Use a narrower class (`limit_violation`, `null_pointer_failure`, `out_of_bounds_read`, `race_condition`, etc.) whenever the evidence supports it; `software_defect` is the fallback only when none of them fit. |
| `security_compromise` | Unauthorized or malicious activity compromises a system or security boundary and directly produces the incident's failure. |
| `hardware_data_loss` | Failure of hardware or firmware causes stored or in-flight data to be lost or irrecoverably corrupted. |
| `consistency_anomaly` | Components observe or maintain mutually inconsistent state, causing incorrect operation or preventing safe progress. |
| `other` | No existing mechanism class fits; every use of `other` must be reviewed before the gold set is frozen. |

---

## `detection_method` — ADR 002 / `app.extract.taxonomy.DetectionMethod`

Single-valued: the method responsible for the **first acknowledged
detection** of the incident (the first signal that caused the
organization to recognize or begin responding to it) — not every signal
that appeared during the incident, and not the earliest reconstructable
symptom (ADR 002 §4).

| Value | One-line definition |
| --- | --- |
| `monitoring` | An automated system (alerting, synthetic probe, health check, anomaly detection, automated test) produced the first detection signal. `automated` was merged into this class (ADR 002 §1). |
| `internal_manual` | An internal person, other than the one performing the triggering operation, manually discovered the incident (e.g. through normal system use). |
| `operator` | The person performing the triggering operation directly noticed that it had gone wrong (split out of `internal_manual`, ADR 002 §3). |
| `customer_report` | A customer or external user's report was the first detection signal. |
| `ambiguous` | Multiple detection paths are documented but the document does not establish which one first caused acknowledgment. |
| `unknown` | The document does not provide enough detection information to determine a method. |

---

## Sources

- [app/extract/taxonomy.py](../app/extract/taxonomy.py) — the enforced enums and verbatim definitions (source of truth)
- ADR 008 — [docs/adr/008-taxonomy-revision.md](../docs/adr/008-taxonomy-revision.md) (`trigger`, `mechanism` revision)
- ADR 006 — [docs/adr/006-taxonomy-classes.md](../docs/adr/006-taxonomy-classes.md) (original `mechanism` class list)
- ADR 002 — [docs/adr/002-detection-method.md](../docs/adr/002-detection-method.md) (`detection_method`)
- ADR 006 §5 — anomalous-condition rule referenced by ADR 008 for `trigger`
- [M5_PROTOCOL.md](M5_PROTOCOL.md) §3 — labeling protocol (`unknown` / `null` rules, blind labeling)
