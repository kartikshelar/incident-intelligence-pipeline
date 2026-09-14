### 1. The decision

I’m splitting the trigger into two fields: **the initiating change/event** and **the immediate failure mechanism**. This decision is about the **structure of the taxonomy only**; the actual class lists and enum values for these fields are deferred to **01b**.

The initiating trigger is **nullable**, because some incidents have no identifiable initiating change. The failure mechanism is **required**, because every included incident should identify what actually failed. I’m also deleting `change_induced` as a separate field because it is derivable from whether an initiating trigger is present.

### 2. Why

My own labels kept naturally collapsing both ideas into one phrase. Five of the ten were effectively two-part explanations: Cloudflare had a database permissions change that produced an oversized feature file; Datadog had a systemd update that wiped Cilium routes; GitHub had network maintenance that triggered a cross-region database failover; GCP had bad quota data that triggered a null-pointer crash; and Slack had Consul maintenance that churned caches and overloaded Vitess. A single trigger field therefore forces two different causal ideas into one label.

### 3. What I rejected

I rejected **T1**, a single enum for the initiating event, because it captures what changed but loses the mechanism that turned the change into an outage. I rejected **T2**, a single enum for the failure mechanism, because it captures what technically failed but loses the operational event that exposed or triggered it.

**T3 also splits the field in two, but along a different cut.** Its `initiator` axis (`internal_change`, `external_change`, `no_change`) describes who or what initiated the change, while `artifact` (`code`, `config`, `data`, `infrastructure`, `human_procedure`) describes what kind of thing changed. Neither captures what subsequently failed. Under T3, for example, Cloudflare becomes `internal_change` + `config`, which is accurate but loses the fact that the proxy panicked when the generated feature file exceeded its size limit. My split instead keeps the initiating change in one field and the resulting failure mechanism in the other, matching the distinction that repeatedly appeared in the raw labels.

I do agree with T3 that two fields are preferable to one, and with its treatment of `change_induced` as derivable rather than independently labeled. The disagreement is therefore about **where to make the two-field cut**, not about whether two fields are needed.

### 4. The hard cases

GCP and Roblox show why the boundary is not always clean. For GCP, the policy change is the initiating event and the null-pointer crash is the immediate failure. For Roblox, enabling Consul streaming is the initiating change, while contention under load is the immediate failure; the separate BoltDB pathology is recorded as a contributing factor.

AWS demonstrates why the initiating trigger must be nullable: there was no initiating change identified in the incident; the DNS race condition was latent, so `trigger` is null and the race condition is captured by `mechanism`.

When multiple changes could plausibly count as the trigger, I will choose the **change closest to the failure that was necessary to activate the failing path**, rather than simply the earliest event. The failure mechanism will be **single-valued**, not a list: the primary mechanism that directly produced the service failure goes in `mechanism`, while additional mechanisms belong under contributing factors.

### 5. How I’d know I was wrong

After 01b defines the enum classes, I will re-label the same ten documents independently and require agreement on **at least 8 of 10 documents for each field** between the original and second pass. Deferring this test until the enums exist avoids pretending that exact agreement can be measured meaningfully against the current free-text labels; if either field falls below 8 of 10, I will treat that as evidence that its classes or decision rules need revision.
