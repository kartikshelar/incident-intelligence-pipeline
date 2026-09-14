# M0 Spike — Findings (section 4a)

**Date:** 2026-09-13
**Produced by:** Claude Code (Fable 5.1), one reading pass per document. No model extraction was run.
**Scope:** PROJECT_BRIEF §4a items 1–5 only. §4b (blind labels) untouched. No [DERIVE] item is answered here; §6 of this file proposes taxonomies and stops.

> **Do not use anything in this file as a label.** Every "stated / inferred / absent" call below is one reader's
> judgment about *where the information lives in the text*, not about what the right value is. Kartik's
> `eval/blind_labels_m0.json` is the only ground truth this project has.

---

## 1. Corpus

10 documents, 10 orgs, 5 formats. Raw bytes are in `spike/raw/`, extracted text in `spike/text/`,
provenance (URL, sha256, fetch time, byte counts) in `spike/corpus_manifest.json`.

| id | org | incident | format | raw bytes | text chars | chrome % |
|---|---|---|---|---|---|---|
| A | Cloudflare | 2025-11-18 core proxy outage | blog, heavy chrome | 543,683 | 18,114 | 96.7 |
| B | GitLab | 2017-01-31 database deletion | blog, no `<article>`/`<main>` | 231,573 | 24,871 | 89.3 |
| C | AWS | 2025-10-19/20 us-east-1 DynamoDB DNS | vendor post-event summary page | 365,938 | 24,080 | 93.4 |
| D | Datadog | 2023-03-08 multi-region outage | blog | 280,340 | 12,443 | 95.6 |
| E | GitHub | 2018-10-21 MySQL failover | blog | 184,331 | 18,192 | 90.1 |
| F | CrowdStrike | 2024-07-19 Channel File 291 | **PDF**, 12 pages | 179,183 | 29,524 | n/a |
| G | Google Cloud | 2025-06-12 Service Control crash loop | **status page** incident log | 49,860 | 20,855 | 58.2 |
| H | Slack | 2022-02-22 cache/Vitess cascade | blog (WordPress) | 78,143 | 17,328 | 77.8 |
| I | Roblox | 2021-10-28 73-hour Consul outage | blog | 396,347 | 32,275 | 91.8 |
| J | Kubernetes test-infra | 2019-02-08 Prow partial outage | **markdown in repo** | 15,904 | 15,904 | 0 |

Selection was hand-picked for spread, not random. The danluu index (saved as `spike/raw/danluu_index.md`,
~400 links) is a sufficient pool for the 40-document gold set in §8, but §8 requires a recorded selection
filter, which this spike does not define.

Format coverage against the brief: markdown in a repo (J) ✔, vendor status-page writeup (G; C is the
AWS equivalent) ✔, PDF (F) ✔, blog with heavy HTML chrome (A, B, D, E, H, I) ✔.

---

## 2. What broke in parsing before any extraction happened

These are M2 findings but they surfaced here, so they are recorded here.

1. **"First `<article>`" is the wrong heuristic.** GitHub (E) and Slack (H) both have several `<article>`
   elements; the first is an author-bio card (E) or a related-post card (H). v1 of the extractor returned
   0 and 124 characters respectively with HTTP 200 and no error. Fix used: take the longest of all
   `<article>`/`<main>` candidates. GitLab (B) has neither tag and falls through to `<body>`, so its text
   opens with nav strings ("Close", "Suggestions", "GitLab Duo Agent Platform"). A silent empty-extraction
   must be a hard failure in M2, not a `partial` record.
2. **Titles live outside the content container.** For A, D, E the `<h1>` is in a stripped `<header>`, so
   the extracted body has no title. Title must come from `<title>` / `og:title` metadata, never from body
   text. For G the page title is the auto-generated status headline ("Multiple GCP products are experiencing
   Service issues.").
3. **Related-post cards leak dates and incident names into the text.** E's tail contains "The August 17
   outage, and the work ahead" and "GitHub availability report: August 2026"; I's tail has three 2026
   headlines; H's has "Previous Post / Next Post". A date-hungry extractor will pick these up. Truncate
   at the first "Related"/"Recommended"/"Share" marker or use the content container's own boundaries.
4. **PDF page furniture is injected mid-sentence.** pypdf emits `Page 4 of 12  2024-08-06` between
   "These fixes" and "are being backported". The date in the footer (publication) is not the incident date.
   Pages 8–12 of F are a kernel crash dump (hex registers, stack) — roughly a third of the text is noise
   for every schema field.
5. **Inline code fragments become one token per line.** A's SQL query and B's `pg_basebackup` mentions are
   split across 10–20 lines each because `<code>` is inline. Harmless for an LLM, but it inflates line
   counts and breaks any regex-based timeline parser.
6. **Status pages are reverse-chronological running logs.** G contains a full Incident Report (top), a
   Mini Incident Report with different numbers, and 14 live updates. Preliminary statements are later
   contradicted (see §4, `occurred_at`). The extractor must know which section is authoritative.
   G also exposes `JSON History` and `Schema` links: status pages often have a machine-readable feed,
   which is a cheaper and more reliable source than the HTML.
7. **Chrome ratio is 78–97 % for every blog.** Raw storage in MinIO is fine, but cost-per-document
   (§9) must be computed on extracted text, not raw bytes, or the metric is meaningless.
8. **Timezones are all over the place.** UTC (A, B, D, E), PDT (C), "US/Pacific" (G), "Pacific Time" (H),
   PST (I, J), and J's chat-log appendix is in UTC while its body is PST. C's incident starts 11:48 PM
   and ends the next calendar day.
9. **Same-incident, multiple documents.** F explicitly defers detection/impact to the earlier PIR.
   G has two reports in one page. B links a live Google Doc. `content_hash` idempotency is about
   *documents*; incident identity is a separate problem the draft schema does not have a column for.

---

## 3. Field audit across the 10 documents

Legend: **E** stated explicitly · **I** requires inference from the text · **A** absent from this
document · **C** the document contradicts itself. Per-cell evidence quotes are in `spike/field_audit.json`.

| field | A | B | C | D | E | F | G | H | I | J | E / I / A / C |
|---|---|---|---|---|---|---|---|---|---|---|---|
| org | E | E | E | E | E | E | E | E | E | E | 10 / 0 / 0 / 0 |
| title | E | E | E | E | E | E | I | E | E | E | 9 / 1 / 0 / 0 |
| occurred_at | C | E | E | E | E | E | C | I | E | E | 7 / 1 / 0 / 2 |
| affected_services | E | E | E | E | E | E | E | E | E | E | 10 / 0 / 0 / 0 |
| trigger (event described) | E | E | E | E | E | E | E | E | E | E | 10 / 0 / 0 / 0 |
| contributing_factors | I | E | I | E | I | E | I | E | E | E | 6 / 4 / 0 / 0 |
| detection_method | E | I | I | E | E | A | I | E | I | E | 5 / 4 / 1 / 0 |
| time_to_detect | E | E | I | E | E | A | E | I | I | I | 5 / 4 / 1 / 0 |
| time_to_mitigate | E | E | E | E | E | A | C | A | E | E | 7 / 0 / 2 / 1 |
| blast_radius (quantified) | I | E | I | I | E | A | I | I | E | I | 3 / 6 / 1 / 0 |
| change_induced | E | I | E | I | I | E | E | E | E | E | 7 / 3 / 0 / 0 |
| remediation_actions | E | E | E | E | E | E | E | E | E | E | 10 / 0 / 0 / 0 |

`id`, `source_url`, `content_hash`, `extraction_status`, `per_field_confidence` are system fields and
were not audited.

### Field notes

**org** — always stated, but the column is under-specified twice. I is a Roblox incident whose root cause
is inside HashiCorp's Consul/BoltDB and the post is co-authored by HashiCorp. J is an open-source project
team, not a company. `org` conflates "who published", "whose service", and "whose code".

**title** — present in the source 10/10, but only 7/10 survive body-only extraction (see §2.2). G needs
a synthesised title.

**occurred_at** — the date is stated 10/10. The *time* is where it falls apart. A says impact "began at
11:20 UTC" in prose and "Impact starts 11:28" in its own timeline table. G gives three start times in one
page (10:45, 10:49, 10:51) and two end times (13:49, 18:18). B has no single moment: snapshot 17:20, load
19:00, deletion ~23:30, data-loss window 17:20–00:00. I's trigger (streaming enabled 10/27 14:00) is 24
hours before detection (10/28 13:37) and 27 hours before player impact (16:35). "When did it occur" has at
least five defensible anchors: change applied, first symptom, first customer impact, detected, declared.

**affected_services** — stated 10/10, but granularity spans 1 (H) to 100+ (G). C ends its list with
"Refer to the event history for the full list", so the explicit list is known-incomplete. G has two
different lists in the same page. D's axis is *regions*, not services. F's affected thing is *customers'
Windows machines*, not a vendor service. Multi-label F1 on this field will measure vocabulary agreement
more than extraction quality unless each org gets a canonical service list.

**trigger** — the triggering event is described explicitly in all 10. That is the good news. The bad news
is classification: 8/10 involve some change, and the *kind* of change differs in ways that any taxonomy
must adjudicate. G has two candidate triggers (May 29 code without a flag; June 12 policy data with blank
fields). I has two ("root cause was due to two issues"). H's author explicitly distinguishes "what
triggered" (Consul restarts) from what caused it (scatter query + cache dependency). See §6.

**contributing_factors** — an enumerated list exists in 6/10 (B: 5-Whys; F: numbered Findings; I: summary
bullets; D: "the aggravating factor is"; H: a one-sentence list; J: "What went poorly"). In the other 4 the
factors are spread across narrative sections (C spreads them across six per-service sections). Three traps:
(1) the remediation list is usually the *inverse* of the factor list, which is a useful heuristic and also a
way to hallucinate factors the author never claimed; (2) J's most important technical factor (no
CPU/memory requests on service deployments) appears only in the chat-log appendix, not in the write-up;
(3) authors disagree on what "root cause" means: D names one, I names two, F calls it a "confluence",
H quotes Cook to reject the concept. Factor counts range 3–10. The brief's prediction of low agreement
looks right.

**detection_method** — explicit mechanism in 5/10. The draft enum breaks in four distinct ways:
- **monitoring vs automated** is not a distinction any document makes. A: "first automated test detected
  the issue"; D/E: "internal monitoring". Same thing, two words.
- **multi-valued.** H: user tickets, internal users, and pages "almost simultaneously". A single enum
  forces a choice the author refused to make.
- **actor self-detection.** B: the engineer noticed their own `rm` "a second or two" later. That is neither
  monitoring nor customer report nor a manual audit.
- **dismissed early signal.** J: an OOMKilled log at 13:46 was seen and shrugged off; the outage was
  "detected" at 16:04 when a human noticed the bot was unresponsive. Which is the detection?
- F is genuinely absent because the RCA defers to the PIR.

**time_to_detect** — stated as a duration in only 2/10 (D "three minutes after", G "within 2 minutes").
Derivable by arithmetic from two explicit timestamps in 3 more (A, E, B's "a second or two"). The rest
depend on which anchor pair you pick (I: 24 h from trigger or ~0 from impact; J: 2h18m or 2h44m).

**time_to_mitigate** — stated or derivable in 7/10, absent in 2 (F, H has no end time at all), and G
contradicts itself ("Duration: 3 hours" vs 10:51–18:18). But 6/10 have *multiple* endpoints that differ by
hours to days: A 14:30 vs 17:06; C 2:40 AM (DynamoDB) vs 2:20 PM (event) vs Oct 21 4:05 AM (Redshift);
D 09:13 (web) vs +27 h (all services) vs +48 h (backfill); E 12h20m (primaries) vs 24h11m (green). A single
interval loses mitigated / resolved / backfilled.

**blast_radius** — qualitative statement 10/10. A customer-facing number in 3/10 (B: 5,000 projects / 700
users; E: 5M webhooks, 80k builds, 200k dropped; I: players at 50 % of normal). Infra-only numbers in 2
(D: "tens of thousands of nodes"; J: 600 pending pods). None in 5. Units are incomparable across orgs.
The brief was right.

**change_induced** — clean yes 6/10, clean no 1/10 (C: latent race). Ambiguous 3/10: B is a manual
command, not a deploy; D is an OS auto-update nobody at Datadog initiated; E is physical hardware
maintenance. The boolean collapses *who initiated* and *what kind*, and it is fully determined by
`trigger_class` under any taxonomy with a change axis, so it is either redundant or inconsistent.

**remediation_actions** — explicit 10/10. But completed vs planned vs "we will investigate" differs: F
dates each item; B links issue numbers; A is all future tense; G's two reports list different items. And
mitigation-during-incident (rollback, throttle, red button) is mixed with remediation-after (rewrite,
audit, new DC). J's single action item is "be more careful".

---

## 4. Where the draft schema breaks on real text

Numbered so an ADR or the v0.1 schema can cite them.

1. **`occurred_at date` is five different things** (§3). Two documents contradict themselves about it.
   Proposal: a small set of typed anchors — `change_at`, `impact_start`, `detected_at`, `mitigated_at`,
   `resolved_at` — each nullable, each carrying a precision marker (`exact | minute | hour | day |
   approximate`) and the original timezone string. `occurred_at` becomes a view (`impact_start` else
   `detected_at` else date).
2. **`time_to_detect` / `time_to_mitigate interval`** cannot represent "within minutes", "three minutes
   after", "~2h 40 mins", or "a second or two", and cannot say which two anchors were subtracted. Proposal:
   derive both from the anchors in (1) rather than extracting them as fields, and store the free-text
   phrase when the author gives one. Absent anchors give NULL, not a coerced number.
3. **`detection_method enum`** needs: `monitoring` and `automated` merged; multi-valued (`enum[]`) or a
   `primary` + `also` pair; a value for operator self-detection; and a distinction between first signal
   and first acknowledged signal (J). 1/10 is genuinely absent, 4/10 need inference.
4. **`change_induced boolean`** is redundant with `trigger_class` and ambiguous in 3/10. Whether to keep
   it depends on DERIVE-01, so it is not resolved here.
5. **`affected_services text[]`** needs an org-scoped canonical vocabulary or the multi-label F1 in §8 is
   noise. Also needs a `list_is_complete boolean` (C) and must tolerate "all services" / regions (D).
6. **`org text`** should be split or annotated: `publisher_org`, `affected_org`, optional `vendor_org`
   (I), and OSS projects (J).
7. **`title`** must be sourced from document metadata, with a synthesised fallback for status pages (G).
8. **`contributing_factors jsonb {text, normalized_class}`** — `normalized_class` presumes a factor
   taxonomy that does not exist yet and is a second DERIVE-01-sized decision. The `text` half is fine.
   Add `source_section` (summary / timeline / appendix) because the same document says different things in
   different sections (A's 11:20 vs 11:28; J's chat log).
9. **`remediation_actions text[]`** should split into `mitigations` (during) and `remediations` (after),
   each with `status ∈ {done, planned, proposed}` and optional date. Every document supports this split.
10. **`blast_radius {qualitative, quantitative_if_stated}`** is fine as designed; the finding is that
    `quantitative_if_stated` will be NULL in 7/10 and non-comparable in the other 3. Eval must score
    absent-vs-wrong separately, as §8 already says.
11. **One row per incident is wrong for 2/10.** C is three impact periods across ~10 services; G is one
    incident with two reports and 14 updates. Either `incident → impact_periods[]` or accept that the
    extracted record is the *primary* incident and the rest is lossy. Which one is a schema decision, not
    an extraction decision.
12. **Document ≠ incident.** F/PIR, G/mini+full, B/blog+Google Doc. `content_hash` dedups documents;
    nothing in the schema links two documents to one incident. Needs `incident_id` separate from
    document `id`, or an explicit "one document per incident" scope statement in the README limitations.
13. **Cross-org multi-tenancy of the corpus is fake** (relevant to DERIVE-04, not answered here): every
    source is public, and the only "tenant" is the person running the pipeline.

---

## 5. Kill-criteria check (§4d)

- **Usable record from ≥ 6 of 10?** Yes: 10/10 yield org, title, date, trigger description, affected
  services, and remediation. The weakest is F, where detection, timing, and blast radius are absent by
  design because the RCA is a follow-up document.
- **Taxonomy applicable consistently?** Cannot be judged until Kartik's blind set exists and §4c
  self-agreement is measured. What this spike can say: every candidate in §6 has at least two documents
  that land on a boundary (G, H, I, B).
- **Verdict:** no kill signal. The vertical is viable. The brief's three "known-hard" fields are hard for
  exactly the reasons predicted, and `detection_method` should be added to that list.

---

## 6. Candidate trigger taxonomies — PROPOSE ONLY (DERIVE-01 stays open)

Three candidates. No recommendation is made. The "where the 10 land" columns exist to show where each
taxonomy is *ambiguous*, not to label anything; the cells marked `?` are the argument.

### T1 — Proximate-change taxonomy (8 classes)

Classify by *what was changed immediately before impact*.

`code_deploy` · `config_change` · `data_or_content_push` · `infra_maintenance` · `external_or_auto_update`
· `operator_action` · `latent_defect_no_change` · `load_or_capacity_shift`

| A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|
| config_change | operator_action | latent_defect | external_or_auto_update | infra_maintenance | data_or_content_push | data_push **or** code_deploy ? | config_change (rollout) | config_change **or** load_shift ? | code_deploy |

- **For:** matches how 8/10 authors narrate the trigger; makes `change_induced` derivable; each class is
  observable in a timeline entry; easy to explain to an interviewer.
- **Against:** config vs data vs code is blurry (A's "permissions change" is config, the feature file is
  data, the `unwrap()` is code); two-trigger incidents (G, I) need a "primary trigger" rule that the
  labeler must apply consistently; 8 classes with a 40-item gold set gives ~5 per cell, so the confusion
  matrix will be sparse.

### T2 — Failure-mechanism taxonomy (6 classes, adapted from Oppenheimer, Ganapathi & Patterson 2003)

Classify by *what mechanism produced the outage*, using a citable prior study as the counterfactual
baseline the brief asks for ("what did you reject").

`operator_error` · `software_defect` · `bad_config_or_data_propagation` · `overload_or_cascade` ·
`hardware_or_network` · `external_dependency`

| A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|
| bad_config_or_data **or** software_defect ? | operator_error | software_defect | external_dependency **or** bad_config ? | hardware_or_network **or** software_defect (Orchestrator) ? | software_defect **or** bad_config_or_data ? | bad_config_or_data | overload_or_cascade | overload_or_cascade **or** software_defect ? | software_defect |

- **For:** fewer classes, denser confusion matrix; captures the engineering content ("cascading failure",
  "latent race") that T1 throws away; has a published baseline to compare rates against.
- **Against:** it is inherently multi-label — 5/10 land on a boundary because the *trigger* was one
  mechanism and the *outage* was another (F: a data push exposed a software defect; I: a config change
  caused overload via a defect). Mechanism is also the field most likely to show low self-agreement in
  §4c because it requires the labeler to decide what "the" mechanism was.

### T3 — Two independent axes (3 × 5)

Axis 1, **initiator**: `internal_change` · `external_change` · `no_change`.
Axis 2, **artifact**: `code` · `config` · `data` · `infrastructure` · `human_procedure`.

| | A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|---|
| initiator | internal | internal | no_change | external | internal | internal (vendor→customer) | internal | internal | internal | internal |
| artifact | config | human_procedure | code | config **or** infrastructure ? | infrastructure | data | data **or** code ? | config | config | code |

- **For:** each axis is small and separately labelable, so agreement can be measured per axis; the
  `change_induced` boolean becomes exactly `initiator ≠ no_change`; sparse cells are acceptable because
  metrics are per axis, not per cell.
- **Against:** the "artifact" axis inherits T1's code/config/data blur; some of the 15 cells are
  meaningless (`no_change × human_procedure`); two fields to extract instead of one, and the eval in §8
  is written for a single enum with one confusion matrix.

### What the three have in common

Every candidate has G, I, and one of {A, D, F} on a boundary. Any ADR for DERIVE-01 should include a
written rule for **multi-trigger incidents** (pick earliest? pick proximate? allow two?) because the blind
set will contain them. It should also state whether the taxonomy classifies the *trigger* or the
*mechanism* — H's author draws that line explicitly and the two answers differ for 5/10 documents here.

---

## 7. Questions this spike raises (not [DERIVE], but worth an answer before M2/M3)

1. Is the unit of extraction the document or the incident? (§4.11, §4.12). This decides whether C
   becomes one row or four.
2. Which section is authoritative when a document disagrees with itself (A, G)? Proposal: prefer the
   structured timeline over prose, and the latest report over earlier updates, and record which section
   each anchor came from.
3. Should status pages be ingested from their JSON feed instead of HTML when one exists (G)? It would be a
   fourth "format" with near-zero chrome and its own parser.
4. Does `detection_method` join the "known-hard" list in §6 of the brief? On this evidence, yes.

---

## 8. Files produced

| path | what |
|---|---|
| `spike/raw/*` | 10 raw source documents as fetched (HTML / PDF / MD) + danluu index |
| `spike/text/*.txt` | crude extracted text, one per document |
| `spike/extract_text.py` | the throwaway extractor, including the article-first bug and its fix in comments |
| `spike/corpus_manifest.json` | URL, org, format, sha256, byte counts, fetch time per document |
| `spike/field_audit.json` | per-document, per-field E/I/A/C status with evidence quotes |
| `spike/FINDINGS.md` | this file |

Nothing in `eval/` was created or touched.
