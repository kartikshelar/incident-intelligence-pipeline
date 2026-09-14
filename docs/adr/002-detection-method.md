### 1. Merge `monitoring` and `automated`

I would merge `monitoring` and `automated` into **`monitoring`**. The distinction did not survive contact with these documents: Cloudflare’s “automated test” and Datadog’s “internal monitoring” both describe an automated system producing the signal that led to detection.

For this decision, `monitoring` means only that **an automated system produced the detection signal**. Finer distinctions within automated detection—such as synthetic probes, threshold alerts, health checks, or anomaly detection—are not resolved by this ten-document sample. They remain candidates for subdivision in 01b if the 40-document gold set shows that the distinction is meaningful.

### 2. Keep it single-valued

`detection_method` should remain **single-valued** and represent the method responsible for the **first acknowledged detection of the incident**. It is not intended to enumerate every signal that appeared during the incident.

Slack exposes the limit of this rule: customer tickets, internal users, and engineering pages all surfaced problems at roughly the same time, but the postmortem does not establish which one first caused the organization to recognize the incident. I will not break that ambiguity with a precedence rule. Instead, I will distinguish **`ambiguous`**, where multiple detection paths are documented but their ordering or causal role cannot be determined, from **`unknown`**, where the document does not provide enough detection information at all. Slack is `ambiguous`; CrowdStrike is `unknown`.

### 3. Add operator self-detection

GitLab shows that `internal_manual` is too broad if it includes an operator noticing the consequences of their own action. The engineer accidentally deleted the primary database directory and noticed the mistake while the deletion was still running; that is meaningfully different from another employee independently discovering an existing incident through normal system use.

I would therefore distinguish **`operator`** from `internal_manual`: `operator` means the person performing the triggering operation directly notices that it has gone wrong, while `internal_manual` means another internal human discovers the incident manually. The exact enum names remain a 01b decision, but the structural distinction should be preserved.

### 4. Record first acknowledged signal, not first observable signal

`detection_method` should describe the **first signal that caused the organization to recognize or begin responding to the incident**, not the earliest symptom that can be reconstructed afterward. Kubernetes Prow makes the distinction clear: an `OOMKilled` event existed at 13:46, but the incident narrative describes the meaningful detection as the engineer noticing Prow behaving abnormally around 16:04 and investigating.

The same rule should define `time_to_detect`. Otherwise hindsight can make detection performance appear better than it actually was: a log entry or metric that existed but that nobody recognized as an incident signal did not operationally detect the incident.

### 5. Corpus limitation and validation

None of the ten incidents was cleanly detected first through **`customer_report`**. That may simply reflect a small sample dominated by large infrastructure providers with mature internal monitoring, but it may also indicate reporting bias: public postmortems may systematically understate customer-driven detection. I will track this as the corpus expands to 40 documents; if `customer_report` remains absent or unusually rare, that limitation should be documented before interpreting detection-method frequencies.

For each incident, the operational question is: **“What first caused someone responsible for the system to recognize that there was an incident worth investigating?”** After 01b defines the final classes, I should be able to apply that rule consistently to at least **8 of the 10 documents** on a second labeling pass. `ambiguous` should be used when the document gives competing detection signals but cannot establish which produced acknowledgment; `unknown` should be reserved for cases where the necessary detection evidence is absent.
