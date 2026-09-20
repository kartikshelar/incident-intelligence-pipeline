# ADR 012 — External-provider trigger boundary

## Status

Accepted. Amends ADR 008 before M5 scoring.

## Decision

Apply the following rule to `trigger`:

> Use `config_change` when a provider-side configuration, permission, setting, or default change is explicitly documented as the initiating event. Use `external_service_degradation` when a concrete failure or degradation of an external provider service is explicitly documented as the initiating event. Use JSON `null` when the provider-side cause is only suspected, described as an unexplained/anomalous change or condition, or otherwise not established as a concrete initiating event.

This rule distinguishes the provider's role from the evidentiary status of the event. A provider can be the actor without changing the trigger class to a generic external-dependency category, and an unexplained provider-side anomaly must not be promoted into a trigger merely because it appears temporally related to the incident.

The taxonomy classifies what the source establishes, not what objectively occurred. Two incidents with identical underlying causes can receive different labels if one postmortem confirms the cause and the other only suspects it. This is deliberate: the system extracts from documents, and a document's evidentiary limits are a property of the data, not a defect in the schema.

This boundary rule must also be added to the schema's `trigger` description presented to the extractor. Extraction must be re-run after that schema change and before M5 scoring, so the extractor and the gold-set labeler are evaluated against the same rule.

ADR 008 §3's mechanism table lists additions only. The enforced mechanism enum is the sixteen classes in `app/extract/taxonomy.py`, which retains all twelve classes from ADR 006 and adds the four classes introduced by ADR 008.

## Application to the current gold set

- **T (Tarsnap):** `null`. The report describes anomalous S3 failures and says the ultimate precipitating factor only seems to have been an as-yet-undiagnosed change in Amazon S3 behavior; the initiating provider event is not established.
- **U (PythonAnywhere):** `external_service_degradation`. The report explicitly identifies an EBS storage-volume failure, making the external-provider failure concrete and documented.
- **Y (Firefox):** `config_change`. The report explicitly states that GCP deployed an unannounced change making HTTP/3 the default.
- **N:** `external_service_degradation` when the source explicitly establishes the external provider incident as the initiating event; this is distinct from T because T's source only suspects an undiagnosed S3-side change.
- **O (Honeycomb):** `null`. The report explicitly says the trigger remains unknown.
- **X (Twilio):** `null`. The report does not identify a concrete initiating change or external dependency failure.
- **AB (Prow):** `null`. The I/O/load increase is an anomalous condition rather than an initiating event, and no discrete change/event is identified.

This amendment also preserves the ADR 006 §5 rule that anomalous conditions such as `traffic_spike` and `operational_delay` are not triggers.

## Rationale

The gold-labeling pass exposed inconsistent treatment of provider-side incidents across `config_change`, `external_service_degradation`, and `null`. The previous rules did not state clearly whether classification depended on the provider's involvement, the type of provider-side event, or the strength of evidence in the source.

The distinction above resolves that ambiguity using two axes: what kind of event the source documents and whether the source actually establishes that event as the initiator. A documented provider configuration/default change is `config_change`; a documented provider service or infrastructure failure is `external_service_degradation`; a suspected or unexplained provider-side condition remains `null`.

This means T and N may receive different labels even if the underlying real-world cause was similar: the source for N establishes an external-provider incident, while T explicitly leaves the S3-side cause undiagnosed. That difference is intentional because the extraction target is the evidence available in the document.

## M5 implication

This decision is recorded before M5 scoring and must be applied consistently to both sides of the evaluation.

Before scoring:

1. Add this external-provider boundary rule to the schema's `trigger` description that is supplied to the extraction model.
2. Re-run extraction on the evaluation corpus using that updated schema/prompt.
3. Score the new extraction output against the gold set.

Scoring extraction produced under the previous trigger description would confound extraction quality with a specification mismatch, particularly for provider-boundary cases such as T, U, N, and Y.

The trigger enum itself is unchanged; this ADR defines the assignment boundary among existing trigger classes and JSON `null`.
