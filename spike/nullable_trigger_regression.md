# The AWS nullable trigger across schema versions — investigation, not a fix

Written by Claude Code on 2026-09-16 at Kartik's request. Nothing here
changes code, the prompt, or the schema; the last section lists what a fix
would have to decide first.

## Question

ADR-001 §4 uses AWS (manifest id C, `aws.amazon.com/message/101925/`) as
the example of a null `trigger`: no initiating change was identified, the
DNS race was latent. Run 02 (schema v0.1) returned `trigger = null` for it.
Run 04 (schema v0.3, same model, same thinking mode) returned
`trigger.label = "operational_delay"`. Did the read-side schema text change
how the nullable rule is communicated between those versions?

## Method

The text the model reads is the frozen system prompt (`app/extract/prompt.py`)
followed by `wire_schema()` serialised with sorted keys
(`app/extract/llm.py::system_with_schema`). Both were rendered at the commit
each run was made from and diffed pairwise:

| Version | Commit | Run made from it | Wire schema (chars) |
|---|---|---|---|
| v0.1 | `c013888` | run 02 | 10,769 |
| v0.2 | `a67e75f` | run 03 | 10,118 |
| v0.3 | `0e179a5` | run 04 | 10,861 |
| v0.4 | `8bdefcf` | runs 05, 06, 07 | 10,861 |

## Findings

**1. The prompt never changed.** `app/extract/prompt.py` has one commit
(`023e57d`). The prompt half of the system block hashes identically at all
four versions. Its rule:

> `trigger` is the initiating change or event that activated the failing
> path. It is null ONLY when the document identifies no initiating change
> (e.g. a latent race condition that surfaced on its own).

**2. The record-level field description never changed.** In every version
the `trigger` property of `IncidentRecord` is

```json
{"anyOf": [{"$ref": "#/$defs/Trigger"}, {"type": "null"}],
 "description": "Null ONLY when the document identifies no initiating change or event."}
```

Same shape, same sentence, same position.

**3. The `Trigger` object description is the only nullable-related text
that moved.** Pydantic emits the class docstring as the `$defs.Trigger`
description:

| Version | `$defs.Trigger.description` |
|---|---|
| v0.1 | "ADR-001: the initiating change or event that activated the failing path. Nullable at the record level — AWS's latent DNS race had none. When several changes could count, the one closest to the failure that was necessary to activate it wins (ADR-001 §4)." |
| v0.2 | "The initiating change or event that activated the failing path." |
| v0.3 | identical to v0.1 |
| v0.4 | identical to v0.1 |

v0.2's read-side trim removed both the "Nullable at the record level"
sentence and the AWS example; v0.3 restored them verbatim.

**4. The only read-side difference between v0.1 and v0.3 is unrelated to
nullability.** Three written-description fields gained a character limit
in their description text (the limit itself is enforced client-side and
stripped from the wire):

```
- "One sentence: what changed, and who/what did it."              (Trigger.description, v0.1)
+ "One sentence of at most 200 characters: what changed, and who/what did it."  (v0.3)
- "One sentence: what failed and how."                              (Mechanism.description)
+ "One sentence of at most 200 characters: what failed and how."
- "The factor as the author states it."                            (ContributingFactor.text)
+ "One sentence of at most 200 characters: the factor as the author states it."
```

v0.3 → v0.4 changes only `200` to `400` in those three strings.

**5. The AWS trigger against the read-side state of each run.** The
document text is identical across runs (`text_hash` matched; ADR-007 §2),
the model is `claude-sonnet-5` throughout, and runs 02, 03, 04 and 07 all
sent no thinking parameter.

| Run | Schema | Thinking | "Nullable … AWS had none" in wire text | AWS `trigger.label` | Self-reported `confidence.trigger` | Attempts |
|---|---|---|---|---|---|---|
| 02 | 0.1 | default | present | null | 0.80 | 1 |
| 03 | 0.2 | default | **absent** | null | 0.55 | 1 |
| 04 | 0.3 | default | present | `operational_delay` | 0.55 | 1 |
| 05 | 0.4 | adaptive:low | present | null | 0.60 | 2 (retry was an extra key, not the trigger) |
| 06 | 0.4 | disabled | present | `race_condition` | 0.75 | 1 |
| 07 | 0.4 | default | present | null | 0.75 | 1 |

Run 04's non-null value quoted the document's sentence immediately before
the failure — "Right before this event started, one DNS Enactor
experienced unusually high delays needing to retry its update on several
of the DNS endpoints." — and described it as "activating a latent race
condition". Run 06's non-null value is a different thing: its trigger is
the mechanism (`race_condition`, quoting "The race condition involves an
unlikely interaction between two of the DNS Enactors") and its mechanism
is the symptom (`dns_resolution_failure`).

## Interpretation

**The regression is not explained by a change in how the nullable rule is
communicated.** Run 04 read exactly the nullable text run 02 read (the
v0.2 trim had already been reverted). The run that read *less* nullable
text, run 03, still returned null. Run 07, on the same nullable text as
run 04 (v0.4 differs from v0.3 only in a number), returned null again. Of
the four runs at the API's default thinking, one is non-null, and it is
not the one with the weakest wording.

What the data is consistent with instead:

- **AWS sits on the boundary of the rule as written, and single runs land
  on either side of it.** The prompt and the field description both say
  "initiating change **or event**". The AWS text does contain an event
  immediately before the failure — an Enactor experiencing unusual delays —
  and run 04 took it literally. ADR-001 §4 says there was no initiating
  *change*; whether an anomalous operational *condition* (a delay, a load
  spike) counts as an initiating *event* is not defined anywhere the model
  can read. The only steer toward null is the prompt's parenthetical
  ("e.g. a latent race condition that surfaced on its own") and the object
  description's example.
- **The model reports it as borderline.** Self-reported trigger
  confidence fell from 0.80 (run 02) to 0.55 (runs 03, 04) and has stayed
  at or below 0.75 since, in null and non-null runs alike. That number is
  not calibrated (DERIVE-05), but it is the model saying the same thing
  the flip says.
- **The object description names the corpus document.** "AWS's latent DNS
  race had none" tells the model the intended answer for one specific
  document in the spike corpus. That makes AWS a weak test of the rule
  (it is partly answered in the prompt) and does nothing for any other
  document with a latent defect. It has been in the wire text since v0.1
  and did not prevent run 04.
- **Run 06 is a thinking-mode effect, not a wording effect.** With
  thinking disabled the two-field split itself collapsed (mechanism in the
  trigger slot, symptom in the mechanism slot). That is the case the
  `adaptive:low` default decision rests on; it is not the same failure as
  run 04.
- **Nothing enforces the "guarantee".** ADR-001's nullable trigger is a
  capability of the schema (`Trigger | None`), not a rule the validator
  checks; there is no post-hoc check and there cannot be one without a
  definition of "initiating event" that code can test against text. As
  built, the guarantee is one sentence in the prompt and one in a field
  description, and the retry loop never sees a wrong non-null trigger as
  an error.

## What a fix would have to decide first (not done)

1. **Define event vs condition in ADR-001 or 01b.** Does an anomalous
   operational condition with no actor (a delay, a slow node, a traffic
   spike) count as an initiating event, or is a trigger something that
   *changed*? The AWS case turns entirely on this, and it is a labelling
   rule, not a prompt tweak.
2. **Take the AWS example out of the wire text** once the rule is
   defined, so the corpus document is not answered by the prompt and the
   gold-set score for it means something.
3. **Measure with repeats before calling anything a regression.** One run
   per setting cannot separate a wording effect from a coin flip on a
   borderline document. The AWS null rate at a fixed setting over N runs
   is the number that would show whether any wording change moved it.
