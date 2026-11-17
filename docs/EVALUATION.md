# Evaluation

<!-- clf-val:start -->
During development one earlier draft run (different calibration and settings) scored the test split once; that file was deleted and none of its numbers were used for any choice. --final has not been run.

## Classifier comparison (val)

Selection on val; test reported once with --final.

Train 770 tickets, val 176. Text: `features.row_text` (subject + description), secrets masked, TF-IDF word 1-2 + char 3-5 grams fitted on train. Every model is wrapped in sigmoid calibration (3 folds over train, grouped by root_cause).

Winner rule: highest val macro-F1, unless its lead over another candidate is not significant (McNemar p >= 0.05 on val) and that candidate's ECE is at least 0.05 lower; then the better-calibrated candidate wins (several qualify: the higher macro-F1). Within 0.01 macro-F1, the lower ECE wins. Why: the gate uses confidence thresholds, so calibration matters, and in the first val run RF's lead was not significant (McNemar p 0.26 and 0.08) while its ECE was 0.27-0.33. This rule was added after seeing val results, before any final test scoring.

| Label | Model | Macro-F1 | Accuracy | ECE | Brier | P1 recall | Fit s |
|---|---|---|---|---|---|---|---|
| category | nb | 0.678 | 0.727 | 0.116 | 0.156 | - | 0.5 |
| category | lr_C1 | 0.728 | 0.761 | 0.188 | 0.146 | - | 15.7 |
| category | lr_C10 | 0.735 | 0.767 | 0.166 | 0.146 | - | 13.8 |
| category | svm_C0.1 | 0.728 | 0.756 | 0.172 | 0.132 | - | 1.4 |
| category | svm_C1 **(winner)** | 0.773 | 0.795 | 0.191 | 0.142 | - | 1.7 |
| category | rf | 0.830 | 0.830 | 0.267 | 0.186 | - | 5.7 |
| subcategory | nb | 0.554 | 0.608 | 0.182 | 0.174 | - | 1.4 |
| subcategory | lr_C1 | 0.667 | 0.699 | 0.196 | 0.200 | - | 13.7 |
| subcategory | lr_C10 | 0.687 | 0.722 | 0.223 | 0.212 | - | 11.7 |
| subcategory | svm_C0.1 | 0.689 | 0.710 | 0.196 | 0.188 | - | 2.7 |
| subcategory | svm_C1 **(winner)** | 0.733 | 0.750 | 0.242 | 0.209 | - | 2.9 |
| subcategory | rf | 0.808 | 0.812 | 0.333 | 0.203 | - | 6.5 |
| priority | nb | 0.285 | 0.500 | 0.126 | 0.253 | 0.000 | 0.3 |
| priority | lr_C1 | 0.284 | 0.500 | 0.068 | 0.256 | 0.000 | 21.3 |
| priority | lr_C10 | 0.283 | 0.506 | 0.047 | 0.255 | 0.000 | 36.9 |
| priority | svm_C0.1 | 0.285 | 0.500 | 0.084 | 0.256 | 0.000 | 1.5 |
| priority | svm_C1 **(winner)** | 0.291 | 0.517 | 0.060 | 0.262 | 0.000 | 1.1 |
| priority | rf | 0.258 | 0.449 | 0.070 | 0.257 | 0.000 | 6.2 |

| Label | Winner | Val macro-F1 (95% CI) | ECE | Runner-up | McNemar p vs runner-up (val) | Train macro-F1 | Why | Saved |
|---|---|---|---|---|---|---|---|---|
| category | svm_C1 | 0.773 (0.702-0.827) | 0.191 | rf | 0.263 (b=7, c=13) | 1.000 | calibration rule: rf's lead not significant (McNemar p 0.263) and ECE 0.191 vs 0.267 | tfidf-svm_C1-2026-11-17 |
| subcategory | svm_C1 | 0.733 (0.663-0.779) | 0.242 | rf | 0.082 (b=11, c=22) | 1.000 | calibration rule: rf's lead not significant (McNemar p 0.082) and ECE 0.242 vs 0.333 | tfidf-svm_C1-2026-11-17 |
| priority | svm_C1 | 0.291 (0.254-0.327) | 0.060 | svm_C0.1 | 0.629 (b=10, c=7) | 0.732 | highest val macro-F1 | no: rule |

Top category confusions on val (true -> predicted, count):

- Database -> Cloud: 7
- Infrastructure -> Collaboration: 4
- Security -> Collaboration: 4
- Cloud -> Database: 3
- Cloud -> Identity & Access: 3

### Priority: why a rule, not a model

No priority model is saved (`clf_manifest.json`: `labels.priority.use = "rule"`). The best text model (svm_C1) reaches val macro-F1 0.29 and P1 recall 0.00 (train macro-F1 0.73: it fits noise). Always answering P3 gets val accuracy 0.432.

Can a metadata column predict priority on its own? Train purity = share of train rows whose priority is the most common one for their value (optimistic). Val lookup = predict each val ticket with its value's train majority (the fair check: val holds scenarios the lookup never saw).

| Column | Values | Train purity | Val accuracy | Val macro-F1 | Val P1 recall |
|---|---|---|---|---|---|
| (majority P3) | 1 | 0.391 | 0.432 | 0.151 | 0.000 |
| environment | 3 | 0.392 | 0.409 | 0.178 | 0.000 |
| channel | 5 | 0.432 | 0.432 | 0.232 | 0.000 |
| affected_service | 21 | 0.601 | 0.347 | 0.293 | 0.000 |

No column beats the majority's val accuracy and none finds a single P1. affected_service: high purity on train, much lower accuracy on val. Rare values look pure by chance, and the purity does not carry over to unseen scenarios. Priority follows impact x urgency, which the text and these columns do not carry, so priority comes from a rule (TicketIn.impact, plus raising production outages to P1/P2), never from a confident ML label.
<!-- clf-val:end -->

<!-- mttr:start -->
## MTTR: manual baseline (measured) and assisted (estimated)

**Baseline, measured** from `created_at` to `resolved_at` on 980 resolved or closed tickets (skipped: not_resolved 120). The timestamps are simulated (no business hours or holidays), so these hours are illustrative. Mean: 95% bootstrap interval (2000 resamples, seed 0). SLA breached = resolved later than the priority's target (docs/DATA-CARD.md sla_target_hours: P1 4, P2 8, P3 24, P4 72).

| Priority | Tickets | Mean h (95% CI) | Median h | P90 h | SLA target h | SLA breached |
|---|---|---|---|---|---|---|
| P1 | 125 | 4.7 (4.4-5.1) | 4.7 | 7.5 | 4 | 76 / 125 (60.8%) |
| P2 | 344 | 19.7 (18.8-20.7) | 19.9 | 32.9 | 8 | 296 / 344 (86.1%) |
| P3 | 398 | 53.5 (51.2-55.7) | 54.7 | 85.8 | 24 | 343 / 398 (86.2%) |
| P4 | 113 | 114.4 (104.7-124.4) | 112.1 | 185.0 | 72 | 79 / 113 (69.9%) |
| all | 980 | 42.4 (40.1-45.1) | 29.4 | 89.5 | per priority | 794 / 980 (81.0%) |

The synthetic resolution timestamps were not generated to match the SLA targets, so the 81% breach rate is a property of the simulated data, not a finding about a real service desk. The breach column shows the method; in real use it would run on the organisation's own ITSM timestamps.

**Assisted, estimated** (never observed: the assistant recommends, analysts resolve). For each analysed ticket, start from the baseline mean for its priority. A RECOMMEND rated useful saves `diagnosis_share` (0.30, range 0.15-0.45; source: our assumption, not measured: diagnosis/triage as a share of resolution time is 0.30; 0.15-0.45 is the sensitivity range shown with every estimate) x `useful_saving` (0.50) of it. A RECOMMEND rated incorrect costs 0.25 h of review. Everything else (unrated, unresolved, CLARIFY, ESCALATE, BLOCKED) saves nothing. The dashboard applies this to the real results and their latest feedback (`mttr.estimate_assisted`).

What-if table (scenarios, not results): share of tickets that get a RECOMMEND x share of those rated useful (the rest counted as incorrect).

| RECOMMEND share | Useful rate | Baseline mean h | Assisted mean h (range) | Saving % (range) |
|---|---|---|---|---|
| 50% | 50% | 42.4 | 40.9 (40.1-41.7) | 3.6 (1.7-5.5) |
| 50% | 70% | 42.4 | 40.2 (39.1-41.4) | 5.2 (2.5-7.8) |
| 50% | 90% | 42.4 | 39.6 (38.2-41.0) | 6.7 (3.3-10.1) |
| 75% | 50% | 42.4 | 40.1 (39.0-41.3) | 5.4 (2.6-8.2) |
| 75% | 70% | 42.4 | 39.1 (37.5-40.8) | 7.7 (3.8-11.7) |
| 75% | 90% | 42.4 | 38.2 (36.0-40.3) | 10.1 (5.0-15.1) |

The assumptions are ours, not measured. Report the assisted MTTR as an estimate with this range, and replace the scenarios with the real useful rate once analysts have reviewed results.
<!-- mttr:end -->
