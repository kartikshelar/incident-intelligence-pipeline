# ADR 011 — What the review page shows the reviewer

## 1. Decision

The review page shows **candidate passages**, never **evidence**.

A candidate passage is a place in the source a reviewer should look
first, chosen from the field name and the document text alone by fixed
keyword cues (`app/review/candidates.py`). Evidence is a passage offered
in support of a particular value. The page offers the first kind and
refuses to offer the second: it does not locate, highlight, rank, or
otherwise mark the quote the extraction cites, even when that quote is
one of the candidates. The extracted value is shown as the value under
review, including its `quote` part, and nothing more is done with it.

Two consequences are recorded here so they are not undone later:

- Candidate selection is a pure function of `(field, text)`. It takes no
  record, no quote, no confidence. Anything that reintroduces the
  extraction as an input to what the reviewer is shown reverses this
  decision and needs a new ADR.
- Null-field review is inherently lower confidence than value review,
  and M5 must treat accepted nulls accordingly (§4).

The page also shows each field's definition read from the schema's own
description text (`app/review/definitions.py`), a client-side search over
the full source, and a correction form derived from the field's type
(`app/review/forms.py`). Those are usability fixes for the same finding
and are not decisions in the ADR sense; they are listed in §2 because
they are why the finding is closed.

---

## 2. The finding

I reviewed five routed fields on the M4 page as first shipped. Two things
were wrong with it.

**It was not practically reviewable.** A null field, or a low-confidence
field whose cited quote was not in the source, gave me the value and the
full normalised document — up to 24,000 characters — and nothing else.
Judging `detection_method` on the Knight Capital write-up meant reading
the write-up. Judging whether `vendor_org` should really be null meant
reading the whole write-up for a vendor that might not be there. The
queue exists to spend reviewer attention on uncertainty (ADR-010 §2);
this page spent it on scrolling.

**It biased the reviewer.** For a non-null field the page located the
model's own cited quote in the source, highlighted it, and headed the
section "Evidence in the source". I noticed myself checking whether the
model's quote supported the model's value, which is a different question
from whether the value is correct. A reviewer who is shown the model's
justification is checking the argument, not the field.

The fixes, in the order they shipped:

- Field definitions on screen, read from the schema so they cannot drift
  from what the model was told, with the boundary sentences ("an
  anomalous condition is never the trigger") and the siblings a field is
  confused with (`impact_start` vs `change_at`) called out.
- Three to five candidate passages per field, chosen by cues, in document
  order, with the cues that chose them; the model's quote unmarked.
- Search in the source panel with a match count and jump-to-match, and a
  "find in source" link on each candidate.
- A correction form derived from the field's type, so a reviewer never
  types JSON and a null is an explicit toggle.

---

## 3. Why candidates and not evidence

Corrections made on this page are gold-set input (ADR-010 §6). The M5
eval scores the extractor against them. The brief already names the
circularity this creates for model-generated labels (§4b: "it would
measure agreement with the labeler, not correctness") and guards against
it with an unanchored blind set. Showing the reviewer the model's
justification is the same circularity arriving by a different door: the
reviewer's decision becomes a function of the model's reasoning, the
correction rate on plausible-but-wrong values falls, and M5 then
measures how persuasive the extractor is rather than how often it is
right. The bias is silent — a biased accept looks exactly like an
unbiased one in `field_reviews` — so it cannot be corrected for after
the fact. It has to be prevented at the page.

Candidate passages are not free of influence either: the cue lists
encode my assumptions about where each field's answer tends to be
written. The difference is that the cues are fixed, visible on the page
next to each passage, and the same for every document and every
extraction. They can be wrong, and a reviewer can see when they are wrong
and search instead. They cannot be wrong in a way that correlates with
the model's answer, because they never see it.

Keyword cues rather than anything smarter is PROJECT_BRIEF §3: no
embeddings, no vector store, no retrieval model. The selection is a
deterministic function of the text; the same document and field always
produce the same candidates.

### What I rejected

**Keep the highlighted quote but label it honestly.** Calling it "the
model's quote" instead of "evidence" does not remove the anchor; it names
it. Rejected.

**Show the quote only after the reviewer decides.** A two-step page where
the justification appears after the decision is recorded. It preserves
independence but doubles the interaction for no gain the gold set can
use, and a reviewer who has just decided is not going to reverse on
seeing the quote. Rejected as complexity without a consumer.

**Use the model's quote as one cue among the keywords.** Tempting because
the quote is often exactly where the answer is. It makes the candidate
set a function of the extraction, which is the thing being prevented.
Rejected.

**Rank candidates by score instead of document order.** Score order is
also a ranking the reviewer would read as a recommendation, and for a
non-null field the passage containing the model's quote would often
score first, distinguishing it by position. Document order is neutral
and matches how a timeline reads. Rejected.

---

## 4. The null-field asymmetry

Confirming a value needs one passage: if the document says the on-call
engineer was paged, `detection_method: monitoring` is confirmed the
moment the reviewer finds that sentence. Confirming an absence needs the
whole document: `vendor_org: null` is right only if no passage anywhere
names a third party whose component failed, and a reviewer can only
approach that certainty by reading everything or by trusting that the
cues and a search for the obvious words would have surfaced it.

So an accepted null is a weaker label than an accepted value. The
reviewer has checked the places the cues and their own searches
suggested, not the document. Nothing on the page can remove this; the
page can only make the search cheaper (candidates, search, and the
"find in source" links exist largely for this case) and make the
asymmetry visible.

What follows from it, recorded here for M5 rather than built now:

- **Accepted nulls should be reported separately from accepted values**
  when review precision is computed. The pre-registered targets in
  ADR-010 §5 are stated over all routed fields; the null subset will
  carry more undetected error than the value subset, and folding them
  together would flatter precision on the fields that were hardest to
  judge.
- **The blind set is the check.** The M0 blind labels were made without
  a page at all, so they carry no cue-induced bias. Agreement between
  blind labels and page-reviewed labels on the fields both cover, split
  by null and non-null, is the measurement that says whether page review
  of nulls is trustworthy. It needs no new machinery.
- **The queue is not adjusted.** ADR-009 keeps self-reported confidence
  as the routing signal and ADR-010 keeps the provisional floor; this ADR
  changes neither. If the model's own confidence on null fields is
  already lower — ADR-009 §2 notes the AWS null trigger sat at 0.55 to
  0.75 — the queue already sends more nulls to review, which is the right
  direction. Whether it sends enough is an M5 question.

---

## 5. How I'd know I was wrong

- If the correction rate on routed fields is not materially different
  with the quote hidden than it was with it shown, the bias I noticed in
  five reviews was not there in aggregate, and the highlighted quote was
  a reviewability aid I removed for nothing. I have five reviews with the
  quote shown and will have many more without; the comparison is cheap
  but confounded by everything else that changed on the page.
- If page-reviewed null fields agree with the blind labels at the same
  rate as page-reviewed value fields, the asymmetry in §4 is real in
  principle but not in this corpus, and M5 need not split the metric.
- If reviewers routinely open the full source and search for the model's
  quote by hand, the candidates are not doing their job and the cue
  lists need revising against what reviewers actually searched for. The
  search box makes that observable; the searches are not logged today,
  and logging them would be the first thing to add.
