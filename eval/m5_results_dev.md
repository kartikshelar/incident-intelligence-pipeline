# M5 Results — DEV SPLIT ONLY

Scored per M5_PROTOCOL.md §4. Numbers only; no interpretation, no
threshold revision. Run scored: spike/extraction_run_12.json (schema
v0.8, adaptive:low, post ADR-012). Gold: eval/gold_set.json. Split:
eval/split_manifest.json (seed 20260918).

Dev-split document count: 12
Dev-split document IDs: B, E, F, H, L, N, P, R, W, AA, AB, AC

## 4a. Extraction accuracy (dev split, n=12)

### trigger

Accuracy: 10/12 = 0.8333 (83.3%)

Confusion matrix (rows=gold, cols=predicted):

| gold\pred | code_deploy | config_change | content_update | external_service_degradation | infrastructure_maintenance | manual_command | null |
|---|---|---|---|---|---|---|---|
| code_deploy | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| config_change | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| content_update | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| external_service_degradation | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| infrastructure_maintenance | 0 | 1 | 0 | 0 | 4 | 0 | 0 |
| manual_command | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| null | 0 | 0 | 0 | 0 | 0 | 0 | 2 |

Per-document detail:

| doc | gold | pred | correct |
|---|---|---|---|
| B | manual_command | manual_command | True |
| E | infrastructure_maintenance | infrastructure_maintenance | True |
| F | content_update | content_update | True |
| H | infrastructure_maintenance | infrastructure_maintenance | True |
| L | config_change | config_change | True |
| N | external_service_degradation | config_change | False |
| P | null | null | True |
| R | infrastructure_maintenance | config_change | False |
| W | code_deploy | code_deploy | True |
| AA | infrastructure_maintenance | infrastructure_maintenance | True |
| AB | null | null | True |
| AC | infrastructure_maintenance | infrastructure_maintenance | True |

### mechanism

Accuracy: 11/12 = 0.9167 (91.7%)

Confusion matrix (rows=gold, cols=predicted):

| gold\pred | accidental_data_deletion | cascading_overload | hardware_data_loss | out_of_bounds_read | resource_exhaustion | software_defect | unsafe_failover |
|---|---|---|---|---|---|---|---|
| accidental_data_deletion | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| cascading_overload | 0 | 2 | 0 | 0 | 1 | 0 | 0 |
| hardware_data_loss | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| out_of_bounds_read | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| resource_exhaustion | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| software_defect | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| unsafe_failover | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

Per-document detail:

| doc | gold | pred | correct |
|---|---|---|---|
| B | accidental_data_deletion | accidental_data_deletion | True |
| E | unsafe_failover | unsafe_failover | True |
| F | out_of_bounds_read | out_of_bounds_read | True |
| H | cascading_overload | cascading_overload | True |
| L | resource_exhaustion | resource_exhaustion | True |
| N | cascading_overload | cascading_overload | True |
| P | software_defect | software_defect | True |
| R | cascading_overload | resource_exhaustion | False |
| W | software_defect | software_defect | True |
| AA | software_defect | software_defect | True |
| AB | resource_exhaustion | resource_exhaustion | True |
| AC | hardware_data_loss | hardware_data_loss | True |

### detection_method

Accuracy: 9/12 = 0.7500 (75.0%)

Confusion matrix (rows=gold, cols=predicted):

| gold\pred | ambiguous | customer_report | internal_manual | monitoring | operator | unknown |
|---|---|---|---|---|---|---|
| ambiguous | 0 | 0 | 1 | 0 | 0 | 0 |
| customer_report | 0 | 2 | 0 | 0 | 0 | 0 |
| internal_manual | 0 | 1 | 1 | 0 | 1 | 0 |
| monitoring | 0 | 0 | 0 | 4 | 0 | 0 |
| operator | 0 | 0 | 0 | 0 | 0 | 0 |
| unknown | 0 | 0 | 0 | 0 | 0 | 2 |

Per-document detail:

| doc | gold | pred | correct |
|---|---|---|---|
| B | internal_manual | operator | False |
| E | monitoring | monitoring | True |
| F | unknown | unknown | True |
| H | monitoring | monitoring | True |
| L | monitoring | monitoring | True |
| N | unknown | unknown | True |
| P | monitoring | monitoring | True |
| R | customer_report | customer_report | True |
| W | ambiguous | internal_manual | False |
| AA | customer_report | customer_report | True |
| AB | internal_manual | customer_report | False |
| AC | internal_manual | internal_manual | True |

### Reproducibility-floor check

Pre-registered floors (cross-run reproducibility, already measured):
- mechanism: 86.7%
- trigger: 90.0%

- mechanism: accuracy 0.9167 (91.7%) vs floor 86.7% -> ABOVE FLOOR (flagged per §4a)
- trigger: accuracy 0.8333 (83.3%) vs floor 90.0% -> at or below floor

## 4b. Confidence calibration (ADR 009 §5, dev split)

### trigger

n with confidence recorded: 12

Reliability diagram (10 fixed-width bins):

| bin | n | mean confidence | accuracy |
|---|---|---|---|
| [0.0,0.1) | 0 | — | — |
| [0.1,0.2) | 0 | — | — |
| [0.2,0.3) | 0 | — | — |
| [0.3,0.4) | 0 | — | — |
| [0.4,0.5) | 1 | 0.4500 | 1.0000 |
| [0.5,0.6) | 1 | 0.5500 | 1.0000 |
| [0.6,0.7) | 3 | 0.6333 | 0.6667 |
| [0.7,0.8) | 4 | 0.7500 | 0.7500 |
| [0.8,0.9) | 3 | 0.8333 | 1.0000 |
| [0.9,1.0] | 0 | — | — |

ECE (trigger): 0.1333

Mean confidence on correct (trigger): 0.7000 (n=10)
Mean confidence on incorrect (trigger): 0.7000 (n=2)
Gap (correct - incorrect): 0.0000
0.05 separation criterion: FAIL (within 0.05 — not separating, per ADR-009 §5)

### mechanism

n with confidence recorded: 12

Reliability diagram (10 fixed-width bins):

| bin | n | mean confidence | accuracy |
|---|---|---|---|
| [0.0,0.1) | 0 | — | — |
| [0.1,0.2) | 0 | — | — |
| [0.2,0.3) | 0 | — | — |
| [0.3,0.4) | 0 | — | — |
| [0.4,0.5) | 0 | — | — |
| [0.5,0.6) | 1 | 0.5500 | 1.0000 |
| [0.6,0.7) | 2 | 0.6250 | 1.0000 |
| [0.7,0.8) | 5 | 0.7200 | 0.8000 |
| [0.8,0.9) | 1 | 0.8500 | 1.0000 |
| [0.9,1.0] | 3 | 0.9167 | 1.0000 |

ECE (mechanism): 0.1667

Mean confidence on correct (mechanism): 0.7500 (n=11)
Mean confidence on incorrect (mechanism): 0.7500 (n=1)
Gap (correct - incorrect): 0.0000
0.05 separation criterion: FAIL (within 0.05 — not separating, per ADR-009 §5)

### detection_method

n with confidence recorded: 12

Reliability diagram (10 fixed-width bins):

| bin | n | mean confidence | accuracy |
|---|---|---|---|
| [0.0,0.1) | 0 | — | — |
| [0.1,0.2) | 0 | — | — |
| [0.2,0.3) | 0 | — | — |
| [0.3,0.4) | 1 | 0.3000 | 1.0000 |
| [0.4,0.5) | 1 | 0.4000 | 1.0000 |
| [0.5,0.6) | 2 | 0.5250 | 0.5000 |
| [0.6,0.7) | 3 | 0.6167 | 0.6667 |
| [0.7,0.8) | 1 | 0.7000 | 0.0000 |
| [0.8,0.9) | 4 | 0.8375 | 1.0000 |
| [0.9,1.0] | 0 | — | — |

ECE (detection_method): 0.2375

Mean confidence on correct (detection_method): 0.6444 (n=9)
Mean confidence on incorrect (detection_method): 0.6167 (n=3)
Gap (correct - incorrect): 0.0278
0.05 separation criterion: FAIL (within 0.05 — not separating, per ADR-009 §5)

## 4c. Review queue, uncapped (ADR 010 §5, dev split)

Scored per-field, across trigger/mechanism/detection_method, all 12 dev
documents (36 scored fields total). Routing rule: field is 'routed' if
its self-reported confidence < threshold.

Total scored fields (with confidence): 36
Total wrong: 6
Base error rate: 0.1667 (16.67%)

Threshold sweep (uncapped — every field below threshold is routed):

| threshold | n routed | routed & wrong | precision | recall | precision >= 2.5x base | recall >= 80% |
|---|---|---|---|---|---|---|
| 0.00 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.05 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.10 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.15 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.20 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.25 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.30 | 0 | 0 | n/a (0 routed) | 0.0000 | FAIL | FAIL |
| 0.35 | 1 | 0 | 0.0000 | 0.0000 | FAIL | FAIL |
| 0.40 | 1 | 0 | 0.0000 | 0.0000 | FAIL | FAIL |
| 0.45 | 2 | 0 | 0.0000 | 0.0000 | FAIL | FAIL |
| 0.50 | 3 | 0 | 0.0000 | 0.0000 | FAIL | FAIL |
| 0.55 | 4 | 0 | 0.0000 | 0.0000 | FAIL | FAIL |
| 0.60 | 7 | 1 | 0.1429 | 0.1667 | FAIL | FAIL |
| 0.65 | 11 | 2 | 0.1818 | 0.3333 | FAIL | FAIL |
| 0.70 | 15 | 3 | 0.2000 | 0.5000 | FAIL | FAIL |
| 0.75 | 19 | 4 | 0.2105 | 0.6667 | FAIL | FAIL |
| 0.80 | 25 | 6 | 0.2400 | 1.0000 | FAIL | PASS |
| 0.85 | 27 | 6 | 0.2222 | 1.0000 | FAIL | PASS |
| 0.90 | 33 | 6 | 0.1818 | 1.0000 | FAIL | PASS |
| 0.95 | 35 | 6 | 0.1714 | 1.0000 | FAIL | PASS |
| 1.00 | 36 | 6 | 0.1667 | 1.0000 | FAIL | PASS |

### At the pre-registered M4 operating threshold (0.70)

n routed: 15
routed & wrong: 3
Precision: 0.2000
Recall: 0.5000
Base error rate: 0.1667
Required precision (2.5x base error rate): 0.4167
Precision criterion: FAIL
Recall criterion (>=80%): FAIL
Overall (both must hold): FAIL

## 4d. Trigger accuracy: null vs non-null gold (ADR 011 §4, dev split)

Null-gold trigger docs (n=2): ['P', 'AB']
Null-gold accuracy: 2/2 = 1.0000

Non-null-gold trigger docs (n=10): ['B', 'E', 'F', 'H', 'L', 'N', 'R', 'W', 'AA', 'AC']
Non-null-gold accuracy: 8/10 = 0.8000

Difference (non-null minus null): -0.2000

## Also reported

### Accuracy: 10 anchored M0 documents vs 20 blind documents (restricted to dev split)

Anchored M0 docs in dev split (n=4): ['B', 'E', 'F', 'H']
Blind docs in dev split (n=8): ['L', 'N', 'P', 'R', 'W', 'AA', 'AB', 'AC']

| field | anchored acc | blind acc |
|---|---|---|
| trigger | 4/4=1.0000 | 6/8=0.7500 |
| mechanism | 4/4=1.0000 | 7/8=0.8750 |
| detection_method | 3/4=0.7500 | 6/8=0.7500 |

### Accuracy: seen_before documents vs the rest (dev split)

seen_before=true docs in dev split (n=7): ['B', 'E', 'F', 'H', 'P', 'AA', 'AC']
remaining docs in dev split (n=5): ['L', 'N', 'R', 'W', 'AB']

| field | seen_before acc | rest acc |
|---|---|---|
| trigger | 7/7=1.0000 | 3/5=0.6000 |
| mechanism | 7/7=1.0000 | 4/5=0.8000 |
| detection_method | 6/7=0.8571 | 3/5=0.6000 |

### software_defect rate: gold vs predicted (mechanism field, dev split)

Gold software_defect count: 3/12 = 0.2500
Predicted software_defect count: 3/12 = 0.2500
Gold software_defect docs: ['P', 'W', 'AA']
Predicted software_defect docs: ['P', 'W', 'AA']

