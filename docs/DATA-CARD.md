# VisionQuest synthetic ITSM dataset card

Version 3, generated 2026-11-17 with fixed seeds (curated 20261117, raw 20261118). **100% synthetic**: no real
users, companies, passwords, logs, systems or incidents. Built for development and evaluation only; the records
do not represent real company incidents.

## Three layers

| Layer | Folder | Purpose | Who may read it |
|---|---|---|---|
| **RAW** | `backend/data/raw/raw_tickets.csv` | Dirty records as five source systems would export them | The ingestion pipeline (input) |
| **CURATED** | `backend/data/tickets/`, `backend/data/docs/kb/` | Clean, validated tickets, labels and KB articles | Classifier, similar-ticket search, RAG, evaluation |
| **GROUND TRUTH** | `backend/data/raw/defect_manifest.csv`, `expected_clean_tickets.csv`, `ingestion_expected_outcomes.jsonl` | What ingestion must produce and decide for each raw record | Tests and evaluation of the ingestion pipeline only |

Ground-truth files are answers. Never feed them to the classifier or the ingestion pipeline as input.

## Contents and counts

| File | Records | What it is |
|---|---|---|
| `tickets/tickets.csv` | 1,100 | Curated historical tickets, the 19 columns of the data contract in `docs/PLAN.md` |
| `tickets/labelled_cases.jsonl` | 370 | Gold cases: one per val/test ticket (330) + 40 authored ESCALATE / BLOCKED cases |
| `tickets/retrieval_pairs.csv` | 1,980 | Labelled query/candidate pairs (6 per val/test ticket) |
| `tickets/redteam.jsonl` | 15 | Safety cases (owner: Atheesha; unchanged) |
| `tickets/feedback_examples.csv` | 180 | Synthetic reviewer feedback; **never load it into the search index** (unchanged) |
| `docs/kb/KB-001.md` .. `KB-022.md` | 22 | Simulated KB articles, one per issue family |
| `raw/raw_tickets.csv` | 1,260 | Dirty source records (28 columns, see below) |
| `raw/defect_manifest.csv` | 4,636 | One row per injected defect |
| `raw/expected_clean_tickets.csv` | 1,260 | Expected clean output for every raw record |
| `raw/ingestion_expected_outcomes.jsonl` | 1,260 | Expected action and decisions for every raw record |

## How it was generated

Everything is built by `backend/scripts/datagen/`:

- `content.py`: 22 **issue families** (one per KB article), each with **5 scenarios**. A scenario is one
  distinct root cause with its own wording, error signal, diagnostic check and fix steps (110 in total).
- `authored_cases.py`: 40 hand-written ESCALATE / BLOCKED evaluation cases.
- `build.py`: makes the curated tickets, labels, retrieval pairs and KB articles, then calls `raw.py`.
- `raw.py`: makes the dirty RAW layer from the curated tickets, plus the ground truth. It holds the
  **normalisation contract**: alias tables for priority, status, channel, environment, category, subcategory and
  team, the impact x urgency matrix, and each source system's timestamp convention.
- `validate.py`: read-only checks on all layers (77 checks). Exit code 1 if anything fails.

Curated tickets vary within a scenario (two phrasings, optional error signal and context, environment, channel,
priority, timestamps, status). Duplicate groups are bursts of 2-4 tickets for the same scenario. Groups of 3+
share a problem record. `resolution_steps` are separated by ` | `.

### Regenerate

From `backend`:

```powershell
.\.venv\Scripts\python.exe scripts\datagen\build.py      # writes only files that are new or unchanged
.\.venv\Scripts\python.exe scripts\datagen\validate.py   # must end with "0 failed"
```

`build.py` never overwrites a file whose content differs: it lists those files and exits with code 1. Pass
`--force` only when you mean to replace them. `--out <folder>` writes somewhere else (useful for trying changes).
The same seeds and content always give byte-identical files. `redteam.jsonl` and `feedback_examples.csv` are
never touched.

## Split strategy (no leakage)

- Whole scenarios go to one split: per family 3 train, 1 val, 1 test. Curated result: **train 770, val 176,
  test 154**. Val/test always hold a root cause the classifier has not seen in training.
- Checked zero across splits: subject templates, root causes, fix-step lists, duplicate groups, problem records.
  Largest word overlap of any val/test ticket with any train ticket is 0.40 (shared topic words, not copied text).
- Every raw record inherits its curated ticket's split, and duplicate submissions stay in that ticket's
  duplicate group. Raw split counts: train 853, val 216, test 179, none 12 (junk with no ticket).
- Extra raw records that have no curated ticket (ambiguous, security) are only in val/test, never train.
- `use_for_training` is true only for train-split records that are accepted or normalised **and** whose raw
  labels are trustworthy. Records with missing, wrong or conflicting labels never train the classifier, and no
  evaluation ticket does. The classifier should train on curated `tickets.csv` (train split), not on raw labels.

## RAW schema

`raw_tickets.csv` reuses curated names where the meaning is the same (`created_at`, `resolved_at`, `status`,
`channel`, `category`, `subcategory`, `priority`, `assigned_team`, `affected_service`, `environment`, `subject`,
`description`, `root_cause`, `resolution_notes`, `resolution_steps`). Raw-only fields:

| Field | Meaning |
|---|---|
| `source_record_id` | Stable id `RAW-000001`.., in arrival order. The key for all ground truth |
| `source_system` | `ServiceNow-EMEA`, `Jira-SM`, `Email-Gateway`, `Monitoring-Bridge`, `Legacy-Remedy` |
| `source_ticket_ref` | The source system's own number (formats differ per system) |
| `updated_at`, `sla_due_at` | In the source system's timestamp convention |
| `impact`, `urgency` | `1 - High`, `2 - Medium`, `3 - Low`; priority follows the impact x urgency matrix |
| `related_ref` | Sometimes set on a duplicate submission: the source ref of the original |
| `sla_target_hours` | P1 4, P2 8, P3 24, P4 72 |
| `requester_name/email/phone/username` | Synthetic requester (`@example.invalid`, `+44 7700 900xxx`, `+1-555-01xx`) |

Ground-truth columns (`ticket_id`, `split`, `dup_group_id`, `problem_id`, labels) are deliberately **not** in
the raw file. Timestamp conventions: ServiceNow `YYYY-MM-DD HH:MM:SS` in UTC with no zone written; Jira ISO 8601
with `+0530`; Email RFC 2822 with the sender's offset; Monitoring Unix epoch seconds; Remedy US
`MM/DD/YYYY hh:mm:ss AM/PM` at fixed UTC-06:00.

## Injected defects (4,636 in total; 1,242 of 1,260 records have at least one)

| Group | Defect types (count) | Recoverable? | Expected handling |
|---|---|---|---|
| Format variants | timestamp_format 665, channel_format 434, status_format 427, environment_format 419, priority_format 411 (P1 / Critical / Sev-1 / 1 - Critical...), team_format 304, category_format 269, subcategory_format 102 | Yes | Normalise with the alias tables and source timestamp conventions |
| Text formatting | extra_whitespace 371, html_fragments 254, mojibake 97 | Yes | Strip tags, unescape entities, re-decode cp1252 as UTF-8, collapse whitespace |
| Spelling | spelling_abbreviation_case 281 (pwd, acct, cant, typos, ALL CAPS) | No | Keep the user's words; search must tolerate them |
| Missing values | missing_assigned_team 58, missing_priority 43, missing_category 40, missing_description 41 | Team and category: from service taxonomy; priority: from impact x urgency; description: no | Fill and flag; missing description -> CLARIFY |
| Wrong labels | wrong_category 53, wrong_assigned_team 41 | Yes (except PostgreSQL, which has two subcategories) | Correct from the service taxonomy; mark label untrusted |
| Synthetic PII in text | pii_credential 29 (`FAKE_TEST_PW_####`), pii_email 24, pii_phone 23, pii_username 22 | n/a | Keep in curated text; mask with `common/redact.py` before search, storage, logs or display |
| Duplicates | cross_channel_duplicate 116 (FW:/Re: prefixes, other channel, minutes later) | Yes | MERGE_DUPLICATE into the curated ticket |
| Conflicts | category_service_conflict 22, resolved_before_created 12, priority_matrix_conflict 12, closed_without_resolved_at 9, source_ref_collision 6, invalid_timestamp 5, open_with_resolved_at 3 | No | QUARANTINE for human review, never auto-correct |
| Junk records | test_record 2, monitoring_noise 2, auto_reply 2, empty_record 1, encoding_loss 1, truncated_record 1, placeholder_values 1, future_timestamp 1 | No | QUARANTINE (kept, never silently dropped) |
| New requests | ambiguous_request 14, security_sensitive 18 | n/a | CLARIFY; HOLD_SECURITY (never indexed) -> ESCALATE / BLOCKED |

**Category rule:** if the category disagrees with the affected service but the resolver team agrees with it (or
is blank), trust the service and correct the category. If both category and team disagree with the service,
quarantine the record.

## Ingestion expectations

`ingestion_expected_outcomes.jsonl` gives, per raw record: `curated_ticket_id`, `canonical_ticket_id`,
`ingestion_action`, `quarantine_reasons`, `split`, `use_for_training`, `use_for_index`, `must_redact`,
`expected_outcome`, `expected_reason`, `defects`.

| Action | Records | Meaning |
|---|---|---|
| ACCEPT | 18 | Already clean |
| NORMALIZE | 1,034 | Fix recoverable defects, flag the rest |
| MERGE_DUPLICATE | 110 | Same incident as an existing ticket; link, do not index twice |
| QUARANTINE | 80 | Conflicting, invalid or junk; hold for review (68 linked to a curated ticket, 12 junk) |
| HOLD_SECURITY | 18 | Security-sensitive or attack text; route to security, never index |

`expected_clean_tickets.csv` holds the correct values for each record (for quarantined records: what a reviewer
should restore). Its free text is the raw text with only the recoverable formatting fixed, so spelling noise and
synthetic PII stay in it. Structured fields equal the curated ticket. `label_trusted = False` marks records whose
raw labels were missing, wrong or conflicting.

Expected assistant outcomes on raw records: RECOMMEND 302, CLARIFY 75, ESCALATE 10, BLOCKED 8 (set only for
val/test records and new requests).

## Labels (curated)

- `labelled_cases.jsonl`: RECOMMEND 285, CLARIFY 45 (description too vague), ESCALATE 24 and BLOCKED 16
  (authored: `source: authored`, `ticket_id: null`, inline `ticket`; kept out of `tickets.csv` so attack text
  never enters the search index). `key_sentence` is copied word for word from the gold KB article.
- `expected_reason` reuses `redteam.jsonl` codes (`evidence_available`, `insufficient_detail`,
  `hazard_or_critical`, `prompt_injection`) and adds `destructive_or_privileged_request`,
  `out_of_scope_no_evidence`, `regulated_or_legal`, `repeated_failure`, `credential_request`,
  `other_user_data`, `data_exfiltration`, `harmful_request` (raw security records also use `credential_exposed`).
- `retrieval_pairs.csv`: relevance 2 = same duplicate group, 1 = same scenario, other group, 0 = other family;
  `lookalike = True` marks a relevance-0 candidate with the same category or service.

## Limitations

- Synthetic text is more regular than real tickets: only two phrasings per scenario, a fixed set of defect
  types, and defects injected independently. Real data has correlated, messier problems. Scores will look
  better than on real data.
- Val/test hold out root causes, not issue families: they test new causes of known issue types.
- Duplicate-group relevance exists only within a split. Indexing train and querying test finds no same-group
  tickets; evaluate duplicates within a split or count same-family hits as related.
- Timestamps are simulated (no business hours or holidays). The MTTR baseline is illustrative; never report
  assisted MTTR as observed.
- About 8% of curated priorities are deliberately shifted by one level.
- KB articles are simulated guidance, **not approved procedures of any organisation**.
- ESCALATE / BLOCKED labels and quarantine rules are the authors' judgement of safe behaviour: a proposal for
  the team to review, not verified ground truth.
- All personal data is fake: `example.invalid` emails, fictional phone ranges, `FAKE_TEST_` credentials.

## Alignment with requirements

FR-01 classification labels, FR-02 duplicate groups and retrieval pairs, FR-03/04 KB articles with citable fix
sentences, FR-05 RECOMMEND / CLARIFY / ESCALATE / BLOCKED gold outcomes, FR-06 feedback examples, FR-07 filter
fields, FR-08 masking test strings, FR-09 timestamps for the MTTR baseline. The RAW layer supports demonstrating
ingestion, cleaning, deduplication and quarantine before data reaches the curated layer.
