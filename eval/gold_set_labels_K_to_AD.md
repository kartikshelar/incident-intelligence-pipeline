# M5 Gold-Set Labels — K through AD

**Labeling date:** 2026-09-19  
**Enum source:** `eval/ENUMS.md`  
**Source files:** `eval/label_sources/<ID>.md`


## Labels

| ID | Organization | Trigger | Mechanism | Detection method | Seen before | Note | Source |
|---|---|---|---|---|---|---|---|
| K | Facebook | `manual_command` | `route_deletion` | `unknown` | false | The maintenance command severed the backbone connections. The audit-tool bug allowed the command through, but the immediate technical failure was loss of backbone routing/connectivity, so route_deletion is the mechanism. The source does not establish the first acknowledged detection signal. | `eval/label_sources/K.md` |
| L | Cloudflare | `config_change` | `resource_exhaustion` | `monitoring` | false | The triggering event was deployment of a WAF rule change; the rule caused catastrophic CPU exhaustion. The first documented detection was the synthetic WAF PagerDuty alert. | `eval/label_sources/L.md` |
| M | Atlassian | `manual_command` | `accidental_data_deletion` | `customer_report` | false | The deletion script was executed against production with site IDs instead of app IDs. The first support report from an impacted customer is the documented acknowledged detection; internal monitoring did not detect the deletion. | `eval/label_sources/M.md` |
| N | LaunchDarkly | `external_service_degradation` | `cascading_overload` | `unknown` | false | The incident begins with the documented AWS us-east-1 service outage; excessive retries then overwhelm the streaming service and load balancer. No first acknowledged detection signal is documented in this source. | `eval/label_sources/N.md` |
| O | Honeycomb | `null` | `lock_contention` | `unknown` | false | The document explicitly says it still does not know what triggered the incident, so trigger is JSON null. The technical mechanism is the table-wide cache lock causing unrelated requests to pile up. | `eval/label_sources/O.md` |
| P | incident.io | `null` | `software_defect` | `monitoring` | true | The source does not document an identifiable initiating change or external event; the Pub/Sub message exposes an application defect rather than establishing a trigger. The first documented detection was an H10 monitoring alert. | `eval/label_sources/P.md` |
| Q | incident.io | `database_failover` | `consistency_anomaly` | `customer_report` | true | The incident followed a database failover/promotion that exposed sequence-state inconsistency, producing repeated ID jumps. Customers first reported the anomaly. | `eval/label_sources/Q.md` |
| R | Buildkite | `infrastructure_maintenance` | `cascading_overload` | `customer_report` | false | The database was deliberately downgraded during a maintenance window; under peak load its CPU maxed out and downstream health-check removals created a cascade. PagerDuty failed, and the team learned of the outage from external messages. | `eval/label_sources/R.md` |
| S | Keepthescore (Caspar von Wrede) | `manual_command` | `accidental_data_deletion` | `operator` | false | The author personally deleted the production database and immediately recognized the mistake, so the person performing the triggering operation is the operator detector. | `eval/label_sources/S.md` |
| T | Tarsnap (Colin Percival) | `null` | `resource_exhaustion` | `monitoring` | false | The source reports anomalous S3 failures and says the ultimate precipitating factor only seems to have been an as-yet-undiagnosed change in Amazon S3 behavior. Because no concrete initiating provider change or confirmed provider degradation is identified, trigger is JSON null. Tarsnap monitoring detected the outage. | `eval/label_sources/T.md` |
| U | PythonAnywhere | `external_service_degradation` | `other` | `monitoring` | false | The source explicitly identifies an EBS storage-volume failure as the incident cause. Because this is a concrete failure of an external provider service rather than an unexplained/suspected condition, use external_service_degradation as the trigger. The failure does not fit a narrower mechanism class in the frozen enum, so mechanism is other. | `eval/label_sources/U.md` |
| V | Turso | `config_change` | `consistency_anomaly` | `customer_report` | false | A system change made empty backup identifiers possible; affected databases then shared a backup location and were recreated from the wrong shared backup. The source thanks the customer who notified Turso. | `eval/label_sources/V.md` |
| W | Doug Seven (on Knight Capital) | `code_deploy` | `software_defect` | `ambiguous` | false | A manual software deployment left one server on obsolete Power Peg code, which then produced unbounded child orders. The source documents both unattended internal error emails and external market recognition without establishing which first caused Knight to acknowledge the incident. | `eval/label_sources/W.md` |
| X | Twilio | `null` | `cascading_overload` | `monitoring` | false | The source identifies a loss of connectivity between Redis replicas and the master but no identifiable initiating change or external dependency. Simultaneous resynchronization caused extreme load and cascading service failures; monitoring first alerted the on-call team. | `eval/label_sources/X.md` |
| Y | Mozilla (Firefox) | `config_change` | `software_defect` | `ambiguous` | true | GCP deployed an unannounced HTTP/3 default change, which exposed a latent case-sensitive header bug that looped indefinitely. This is a concrete provider-side configuration/default change, so the trigger is config_change. Mozilla saw a crash-report spike and received internal/external reports without establishing which signal first caused acknowledgment. | `eval/label_sources/Y.md` |
| Z | ESLint | `account_compromise` | `security_compromise` | `customer_report` | true | The maintainer npm account was compromised and used to publish malicious packages. The first documented notification was a user report to the ESLint team. | `eval/label_sources/Z.md` |
| AA | Kubernetes SIG Testing (test-infra) | `infrastructure_maintenance` | `software_defect` | `customer_report` | true | The Prow cluster/node upgrade drained the only Boskos pod and caused the newer buggy image to become active. Users reported the resulting failures; monitoring showed the failure but alerting did not catch it. | `eval/label_sources/AA.md` |
| AB | Kubernetes SIG Testing (test-infra) | `null` | `resource_exhaustion` | `internal_manual` | false | The I/O spike during the code-freeze period is an anomalous condition, not a trigger under ADR 006 §5. No discrete initiating change is identified, so trigger is JSON null. IOPS capacity was exhausted/throttled, and an internal Prow user first reported the problem. | `eval/label_sources/AB.md` |
| AC | King's College London (PA Consulting review) | `infrastructure_maintenance` | `hardware_data_loss` | `internal_manual` | true | A controller hardware failure had no user impact initially; the outage began when engineers replaced the failed component and the storage system went offline, with hardware/firmware failure causing the data loss. The report says the IT team escalated the incident after the storage failure. | `eval/label_sources/AC.md` |
| AD | AAIB (TUI Airways load sheet) | `code_deploy` | `software_defect` | `internal_manual` | true | The reservation/load-sheet system had been upgraded and contained a programming flaw mapping the title Miss to a child. The dispatcher and systems delivery manager discovered the existing problem; neither performed the triggering upgrade, so detection is internal_manual. | `eval/label_sources/AD.md` |

## Gold-set observations

- `trigger_label` uses real JSON `null` for documents with no identifiable initiating change/event; the string `"null"` is invalid.

- External-provider rule: use `config_change` for a documented provider-side configuration/default change; use `external_service_degradation` for a concrete documented external-service/provider failure; use JSON `null` when only an unexplained or suspected provider-side condition/change is documented.

- `traffic_spike`, `operational_delay`, and `race_condition` remain excluded from the trigger enum under ADR 006 §5 / ADR 008: anomalous conditions and mechanisms are not triggers.

- After the K correction to `route_deletion`, `software_defect` occurs on 5/20 documents (25%). This rate should be reported during M5 analysis and checked for conceptual consistency.

- Seven documents are marked `seen_before: true` because their difficulty/model-output discussion occurred before labeling: `P`, `Q`, `Y`, `Z`, `AA`, `AC`, `AD`.


## Frozen enum reminder

Trigger: `config_change`, `code_deploy`, `manual_command`, `os_auto_update`, `infrastructure_maintenance`, `content_update`, `feature_rollout`, `database_failover`, `external_service_degradation`, `account_compromise`, or JSON `null`.

Mechanism: `accidental_data_deletion`, `cascading_overload`, `limit_violation`, `lock_contention`, `null_pointer_failure`, `out_of_bounds_read`, `race_condition`, `resource_exhaustion`, `route_deletion`, `unsafe_failover`, `workload_misrouting`, `software_defect`, `security_compromise`, `hardware_data_loss`, `consistency_anomaly`, or `other`.

Detection: `monitoring`, `internal_manual`, `operator`, `customer_report`, `ambiguous`, or `unknown`.
