# Raw-to-clean ticket ingestion

Owner: Roshan. Code: `backend/app/ingest/` (`contract.py`, `normalize.py`, `pipeline.py`), runner
`backend/scripts/ingest.py`, tests `backend/tests/test_ingest.py`. Data: `backend/data/raw/` (docs/DATA-CARD.md).

It turns a dirty ITSM export (five source systems, mixed formats) into clean records with the curated field
names, and decides what must **not** go into the search index. Every raw record gets exactly one decision;
nothing is dropped silently.

## Run it

From `backend`:

```powershell
.\.venv\Scripts\python.exe scripts\ingest.py              # writes data\processed\ (git-ignored)
.\.venv\Scripts\python.exe scripts\ingest.py --evaluate   # + scores every decision against the ground truth
.\.venv\Scripts\python.exe -m pytest tests\test_ingest.py -q
```

| Output in `data/processed/` | Content |
|---|---|
| `clean_tickets.csv` | ACCEPT / NORMALIZE records: curated field names, plus `action`, `label_trusted`, `flags`, `must_redact` |
| `duplicates.csv` | MERGE_DUPLICATE: record -> the record it duplicates |
| `quarantine.csv` | QUARANTINE: record ID + reason codes (**no ticket text**) |
| `security_hold.csv` | HOLD_SECURITY: record ID + screen codes (**no ticket text**) |
| `ingestion_report.json` | counts, and with `--evaluate` the scores below (**no ticket text**) |

The ground-truth files are read only by `--evaluate`, never by the pipeline.

## Decisions

| Action | Meaning | Goes into the search index? |
|---|---|---|
| ACCEPT | already clean | yes |
| NORMALIZE | recoverable defects fixed with the contract; flags kept | yes |
| MERGE_DUPLICATE | the same incident submitted again; points at the original | no (the original is) |
| QUARANTINE | conflicting, invalid or junk; for a human, never auto-corrected | no |
| HOLD_SECURITY | the security screen flagged it; for security review | no |

## Steps (`ingest()` in `pipeline.py`)

1. **Junk**: empty, placeholder (`N/A`), test posts, auto-replies, bounces, monitoring heartbeats, unreadable
   encoding, truncated text -> QUARANTINE.
2. **Timestamps**: parsed with each source's convention (`contract.TS_CONVENTION`) into UTC ISO 8601.
   Unreadable, future, resolved-before-created, closed-without-resolution, open-with-resolution -> QUARANTINE.
3. **Security screen**: the analysis pipeline's own screen (`app/tickets/screen.py`, reused, not copied).
   Any blocked or safety code -> HOLD_SECURITY. If the screen fails, the record is held (`SCREEN_ERROR`).
4. **Fields**: status, channel, environment, priority, category, subcategory and team mapped with the alias
   tables. Priority comes from the stated value or, when missing, the impact x urgency matrix; a stated priority
   that contradicts the matrix -> QUARANTINE.
5. **Taxonomy** (learned from the curated `tickets.csv`): the documented category rule. If the category
   disagrees with the affected service but the team agrees (or is blank), the service wins and the label is
   marked untrusted. If category and team both disagree, QUARANTINE. A service with two subcategories
   (PostgreSQL) is never guessed: `subcategory_needs_review`.
6. **Source references**: when several records hold one reference, arrival order does not say which one owns it.
   The number's own form can: each system has a fixed form (`contract.REF_FORMAT`, e.g. ServiceNow `INC` + 7
   digits, Remedy `HD` + 12 digits). If exactly one holder's number has its own system's form, it keeps the number
   (flag `source_ref_shared`) and the others go to QUARANTINE. Otherwise every holder goes to QUARANTINE.
7. **Duplicates**: an explicit `related_ref` link, or a reply/forward (`FW:`, `Re:`, `URGENT -`) of a ticket with
   the same subject and service in the previous 2 hours, merged into the one whose description it repeats best.
   Two different people reporting the same outage are **not** merged: they are separate incidents.
8. **Flags**. Value flags make a record NORMALIZE: `missing_description`, `label_untrusted` (the raw label was
   missing, wrong or conflicting: never use it as classifier ground truth), `unrecognised_value`,
   `subcategory_needs_review`. Review notes leave an otherwise clean record ACCEPT: `needs_clarification`
   (description missing, under 3 words, or only repeating the subject) and `source_ref_shared`. `must_redact`
   lists the kinds found by `common/redact.py`, plus "my login is ...".

Privacy: decisions hold IDs, codes and field names only. Requester contact fields are dropped. Clean text stays
unmasked like the curated tickets; it is masked before search, storage, logs or display (`tickets/guard.py`).

The normalisation contract (`contract.py`, `normalize.py`) is shared with the data generator
(`scripts/datagen/raw.py`), so the rules that make the dirty data and the rules that clean it cannot drift.

## Measured on the synthetic raw export (1,260 records)

`scripts\ingest.py --evaluate`. These rules were developed against this same ground truth, so treat the numbers
as development results, not as performance on unseen data.

| Measure | First version | Now |
|---|---|---|
| Action matches ground truth | 95.2% | **95.6%** |
| QUARANTINE precision / recall | 93% / 100% | **100% / 100%** |
| Valid records held for review | 6 (shared references) | **0** |
| Quarantine reason named | 100% | 100% |
| Clean field values (12 fields, where both keep the record) | 100% (subcategory 99.7%) | unchanged |
| Raw labels marked trusted that were really missing or wrong | 0 | 0 |
| Duplicates: merge precision / recall | 97.4% / 67.3% | 97.4% / 68.2% |
| Missing or near-empty description flagged (recall) | 90% | **100%** |
| Injected personal data flagged for redaction (recall) | 81% | 81% (redactor unchanged) |

Per record: 7 records went from wrong to right. One went the other way: RAW-000560 was "right" before only by
accident. A case-sensitive word count saw its ALL CAPS description as empty and flagged it; the ground truth
calls it NORMALIZE for spelling noise, which ingestion keeps as written on purpose.

The clarify flag's measured precision dropped (54% to 44%) because it now also flags curated tickets whose
description only repeats the subject. Those are genuinely too vague (the ground truth expects the assistant to ask
for clarification on 35 of them; the other 31 are training tickets it does not label), so this is a limit of the
measurement, not of the flag.

### Tried and rejected

- **Merging duplicates by identical description** (no reply prefix, no link): 9 of 51 matches were different
  people reporting the same problem (82% precision). Not adopted: it would make automatic merging less reliable.
- **Flagging possible duplicates for review instead** (same text, other channel, within 2 hours): right in 6 of 13
  cases, 5 of 9 even for long descriptions. Removed: a hint that is wrong half the time costs analysts more than it
  saves. Telling a resubmission from a second reporter needs more than text (e.g. the same requester).

## Known gaps (for the owners of the shared parts)

- **Redactor (Atheesha, `common/redact.py`)**: phone numbers need 10-15 digits, so the synthetic US form
  `+1-555-01xx` (8 digits) is not found (13 records). `tests/test_ingest.py` keeps a strict expected-failure test
  for it, which turns into a failure (reminding us to remove it) once the redactor finds these numbers.
- **Security screen (Atheesha, `tickets/screen.py`)**: ingestion reuses it rather than keeping a second rule set.
  On the first run it held 3 of 18 security-sensitive records; re-measure with `--evaluate` after screen updates.
- **Duplicates without a link or reply prefix** (35) are not merged, on purpose (see above).
- **PostgreSQL** (3 records): the raw subcategory belongs to another service, so nothing in the record says
  Connectivity or Performance. Flagged `subcategory_needs_review`, never guessed.
- Spelling noise (`pwd`, `cant`, typos) is kept as written on purpose: search must tolerate it.
