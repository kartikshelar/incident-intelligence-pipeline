# M0 blind-label enum mapping

The 10 M0 documents (`eval/blind_labels_m0.json`) were labeled on 2026-09-13,
before the closed `trigger`/`mechanism` enums existed (ADR-006 §3 closed
`mechanism`; ADR-008 §3 closed `trigger`). Their `trigger_freetext` and
`contributing_factors` free text are mapped here onto the ADR-008 closed
enums so they can enter `eval/gold_set.json`.

This mapping is **anchored, not blind**: the enums were derived in part from
the same 30-document corpus these 10 documents belong to, so a class that
happens to fit an M0 document's freetext is not independent evidence the way
the K–AD blind labels are. Per M5_PROTOCOL.md §3, these 10 keep their
existing M0 `detection_method` labels (already a closed choice at labeling
time — `monitoring`/`internal_manual`/`unknown`, plus `automated` which
ADR-002 later merged into `monitoring`) and are marked
`"anchored_trigger_mechanism": true` in the gold set: only their
`detection_method` label counts as blind ground truth; `trigger`/`mechanism`
do not.

Source for each mapping: the freetext label/contributing factors in
`blind_labels_m0.json`, cross-checked against the source text in
`spike/text/<doc>.txt` where the freetext was ambiguous.

| doc | org | trigger_freetext | -> trigger | mechanism (derived) | -> mechanism | detection_method (M0, unchanged) |
|---|---|---|---|---|---|---|
| `aws_2025-10-20.txt` | AWS | "DynamoDB DNS automation race condition" | `null` | race condition between two DNS Enactors, latent and self-triggered | `race_condition` | `monitoring` |
| `cloudflare_2025-11-18.txt` | Cloudflare | "database permissions change generated an oversized Bot Management feature file" | `config_change` | oversized feature file exceeded the software's hard size limit | `limit_violation` | `monitoring` (M0 said `automated`; ADR-002 merged `automated` into `monitoring`) |
| `crowdstrike_2024-07-19.txt` | CrowdStrike | "bad Channel File 291 update triggered an out-of-bounds memory read in the Falcon sensor" | `content_update` | out-of-bounds memory read, stated explicitly | `out_of_bounds_read` | `unknown` |
| `datadog_2023-03-08.txt` | Datadog | "automatic systemd security update wiped Cilium network routes on running nodes" | `os_auto_update` | routes forcibly deleted by systemd-networkd | `route_deletion` | `monitoring` |
| `gcp_2025-06-12.txt` | Google Cloud | "bad quota policy data triggered a null-pointer crash in Service Control" | `config_change` (the initiating event is the policy-data change inserted into the regional Spanner tables, not the earlier code deploy that only exposed the path) | null pointer on blank policy fields, stated explicitly | `null_pointer_failure` | `monitoring` |
| `github_2018-10-21.txt` | GitHub | "network maintenance partition triggered an unsafe cross-region MySQL failover" | `infrastructure_maintenance` | Orchestrator failed over primaries across regions to a topology the application tier could not safely support | `unsafe_failover` | `monitoring` |
| `gitlab_2017-01-31.txt` | GitLab | "engineer accidentally deleted the primary PostgreSQL database directory" | `manual_command` | production data deleted by an operator command | `accidental_data_deletion` | `internal_manual` |
| `k8s_2019-02-08.txt` | Kubernetes SIG Testing | "Prow client refactor sent test jobs to the service cluster instead of the build cluster" | `code_deploy` | jobs directed to the wrong cluster after the refactor shipped | `workload_misrouting` | `internal_manual` |
| `roblox_2021-10-28.txt` | Roblox | "Consul collapsed under heavy load after enabling its new streaming feature" | `feature_rollout` | BoltDB/Consul write-ahead-log contention under the new streaming path | `lock_contention` | `internal_manual` |
| `slack_2022-02-22.txt` | Slack | "Consul maintenance churned the cache fleet and overloaded the backing database" | `infrastructure_maintenance` | cache churn cascaded into overload of the Vitess datastore | `cascading_overload` | `monitoring` |

## Notes on individual decisions

- **AWS -> `null` / `race_condition`.** Settled by the project's own prior
  record, not re-derived here: ADR-001 names this exact document as the
  worked example for a nullable trigger — "there was no initiating change
  identified in the incident; the DNS race condition was latent, so
  `trigger` is null and the race condition is captured by `mechanism`"
  (`docs/adr/001-trigger-taxonomy.md`). `race_condition` is a mechanism
  class, never a trigger class (ADR-006 §5, ADR-008 §3).
- **GCP -> `config_change`.** Two candidate initiating events are documented:
  the May 29 code/feature deploy (which shipped the vulnerable path but
  never exercised it) and the June 12 policy-data change inserted into the
  regional Spanner tables (which actually activated the null pointer). ADR-001
  §4's rule picks the change closest to the failure that was necessary to
  activate it, not the earliest candidate — that is the policy-data change,
  which is a `config_change`, not a `code_deploy`.
- **Cloudflare detection -> `monitoring`.** M0 used `automated`, a value the
  ADR-002 taxonomy folded into `monitoring` before the enum was closed
  (`app/extract/taxonomy.py` docstring: "`automated` was merged into
  `monitoring`"). No re-labeling judgment involved; this is a straight
  vocabulary substitution of an old value for its ADR-002 successor.

All ten map cleanly onto exactly one class in each closed enum; none required
`other` or a forced guess.
