# ADR 008 — Taxonomy revision after corpus expansion

## 1. Decision

Revise the taxonomy after evaluating ADR 006's mechanism classes and open trigger vocabulary on the expanded 30-document corpus.

For `mechanism`:

* Restore a general `software_defect` class.
* Retain the narrower `limit_violation` and `null_pointer_failure` classes.
* Add `security_compromise`, `hardware_data_loss`, and `consistency_anomaly`.
* Retain `other` as the escape class when no existing mechanism fits.

For `trigger`:

* Replace the open vocabulary with a closed enum derived by clustering the trigger labels observed across the 30-document corpus.
* Do not admit anomalous conditions such as traffic spikes or operational delays as trigger classes. ADR 006 §5 continues to define the trigger as the identifiable initiating change or external event, not the condition it produces.

Both `trigger` and `mechanism` are therefore closed enums after this ADR.

---

## 2. Why

ADR 006's mechanism enum did not transfer adequately beyond the ten documents from which it was derived.

Across the expanded 30-document corpus, 7 of 30 documents (**23%**) landed on `other`, exceeding the pre-registered ceiling of 20%. All seven occurred among the 20 newly added documents: **35% of the held-out expansion set**.

The failure is therefore concentrated in documents that did not contribute to the original taxonomy. ADR 006's classes were derived from ten large-vendor postmortems and fit that corpus substantially better than they fit unseen incidents.

The mechanism taxonomy was **overfit to its derivation corpus**.

The expanded corpus also invalidated the decision to leave `trigger` open. ADR 006 retained free-text triggers because the original ten-document study showed 7-of-10 stability with zero confirmed semantic collisions, and explicitly made a confirmed same-concept/different-string collision a condition for revisiting that decision.

The expanded corpus produced that collision: `internal_config_change` and `config_change` identify the same initiating concept using different strings. Other labels also varied in granularity — for example, operator actions appeared as both `manual_command` and more procedure-specific labels, while infrastructure changes were split across several strings.

The reason for closing `mechanism` therefore now applies to `trigger` as well: canonical classes are required if vocabulary variation is not to become scoring variation.

---

## 3. Taxonomy changes

### Mechanism

ADR 006 §4 rejected `crash_on_bad_input` as a single class because the model applied it to two technically different failures: Cloudflare's hard feature-limit breach and GCP's null dereference. That distinction remains useful. `limit_violation` and `null_pointer_failure` therefore remain separate narrow classes.

What ADR 006 got wrong was assuming those narrow classes exhausted the broader family. Four of the seven `other` cases in the expanded corpus are software defects that do not fit either subtype. Removing the broader category was too aggressive.

Restore that coverage as follows:

| Class                 | One-line definition                                                                                                                             |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `software_defect`     | A defect in software logic or implementation causes failure and no existing narrower software-failure class adequately describes the mechanism. |
| `security_compromise` | Unauthorized or malicious activity compromises a system or security boundary and directly produces the incident's failure.                      |
| `hardware_data_loss`  | Failure of hardware or firmware causes stored or in-flight data to be lost or irrecoverably corrupted.                                          |
| `consistency_anomaly` | Components observe or maintain mutually inconsistent state, causing incorrect operation or preventing safe progress.                            |

`software_defect` is a general class, not a replacement for existing specific classes. When the evidence supports `limit_violation`, `null_pointer_failure`, `out_of_bounds_read`, `race_condition`, or another narrower mechanism, extraction must select that class. `software_defect` is used only when a software defect is identifiable but the evidence does not support an existing narrower mechanism class.

`other` retains its ADR 006 meaning: it is permitted only when no existing mechanism class fits. Every use of `other` must be reviewed before the gold set is frozen.

### Trigger

`trigger` becomes a closed enum.

The enum is derived by clustering the trigger vocabulary observed across the 30-document corpus. Labels that identify the same initiating change or external event are mapped to a canonical class rather than preserving differences in wording or unnecessary specificity.

| Class                          | One-line definition                                                                                                                |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `config_change`                | A change to system configuration, permissions, settings, policies, or other operational configuration data initiates the incident. |
| `code_deploy`                  | A new or changed software build or software version is deployed into an environment and initiates the incident.                    |
| `manual_command`               | An operator manually executes a command or operational procedure that initiates the incident.                                      |
| `os_auto_update`               | An automatically applied operating-system update initiates the incident.                                                           |
| `infrastructure_maintenance`   | Maintenance or replacement work on compute, network, storage, or other infrastructure initiates the incident.                      |
| `content_update`               | New or changed non-code content or data is distributed to a running system and initiates the incident.                             |
| `feature_rollout`              | A feature, capability, or feature-controlled behavior is newly enabled or expanded and initiates the incident.                     |
| `database_failover`            | A database failover, promotion, or equivalent role transition initiates the incident.                                              |
| `external_service_degradation` | Degradation or failure of an external dependency initiates the incident.                                                           |
| `account_compromise`           | Unauthorized compromise of an account or security principal initiates the incident.                                                |

The observed vocabulary is normalized as follows:

* `internal_config_change` and `infrastructure_config_change` map to `config_change`.
* `software_upgrade` maps to `code_deploy`.
* `data_recreation_procedure` maps to `manual_command` when the initiating event is the operator-executed procedure.
* `infrastructure_downgrade` and `hardware_replacement` map to `infrastructure_maintenance`.
* `content_update` and `feature_rollout` remain separate because changing runtime content and enabling a feature are distinct initiating events even when either could previously have been described broadly as a configuration change.

`traffic_spike` is **not** a trigger class. ADR 006 §5 established that an anomalous condition is never itself the trigger. If increased traffic or load is attributable to an identifiable change or external event, that event is the trigger; if no such initiating event is identifiable, `trigger` is `null`.

For the same reason, `operational_delay` is not a trigger class. `race_condition` is also excluded because it describes a failure mechanism rather than an initiating change or external event.

This distinction preserves the causal boundary:

`trigger` = what changed or happened to initiate the failure sequence.

`mechanism` = the technical process through which that initiating event produced failure.

---

## 4. What I got wrong

**Treating the original mechanism enum as transferable after deriving it from ten documents.** ADR 006's classes were derived and initially evaluated on substantially the same corpus. Passing that evaluation demonstrated internal fit, not generalization. The 35% `other` rate on the 20 newly added documents exposed the overfitting.

**Retiring the general software-defect class entirely.** ADR 006 §4 rejected `crash_on_bad_input` because it collapsed Cloudflare's `limit_violation` and GCP's `null_pointer_failure` into one surface-level description. The split was useful, but removing the broader category was too aggressive. Four of the seven new `other` cases require a general software-defect class without fitting either narrow subtype. This ADR restores that broader coverage while retaining the narrow classes when the evidence supports them.

**Keeping `trigger` open.** That decision was supported by the original ten-document experiment, but it was conditional. The `internal_config_change` / `config_change` collision observed after corpus expansion satisfies ADR 006's stated revisit condition. Trigger therefore moves to a closed enum rather than relying on semantic adjudication of free-text labels.

**Allowing anomalous conditions to leak into trigger vocabulary.** The expanded run produced `traffic_spike` and `operational_delay` as trigger strings even though ADR 006 §5 had already established that anomalous conditions are not triggers. Closing the enum makes that rule structural: neither is an allowed trigger class.

---

## 5. How I'd know I was wrong

The previous taxonomy evaluation was weakened by deriving and testing the classes on substantially the same corpus. This ADR replaces that design with held-out evaluation.

When the corpus next expands, the trigger and mechanism enums are frozen **before** inspecting the new documents for taxonomy revision. The newly added documents form the held-out evaluation set. Results are recorded before any classes are added, removed, merged, or split.

For `mechanism`, no more than **20% of held-out documents** may land on `other`. A rate above 20% means the mechanism taxonomy has again failed to generalize and must be revised.

For `trigger`, at least **80% of held-out documents with a non-null trigger** must map to the correct class in the frozen trigger enum. Agreement is exact enum-class equality after applying the trigger boundary defined above. A document that requires a materially new initiating-event concept, or can only be mapped by conflating distinct initiating events, counts as a disagreement.

Legitimate `null` triggers do not count as taxonomy failures: the trigger field remains nullable, and an incident with no identifiable initiating change or external event should remain `null` rather than being forced into an enum class.

Both thresholds are decided before the next corpus expansion is examined. Only after the held-out result has been recorded may those documents be incorporated into the derivation corpus for a subsequent taxonomy revision.
