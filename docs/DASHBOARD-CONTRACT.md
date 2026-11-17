# Dashboard data contract (proposal)

Owner: Roshan (frontend). Status: **proposal, waiting for the backend owners.** The Dashboard
(`frontend/src/pages/DashboardPage.tsx`) is built against this shape through one adapter,
`toDashboardStats()` in `frontend/src/lib/api.ts`. If the backend chooses other names, only that adapter changes.
Until the fields arrive, the page says "Not available yet" and never shows a made-up number.

| Who | What is needed |
|---|---|
| **Akhilesh** | `backend/app/tickets/mttr.py`: the MTTR functions below (FR-09, his file in PLAN.md) |
| **Raswanth** | `db/repo.stats()` returns the fields below (outcome mix and feedback from the database) |
| **Akshay** | `GET /api/tickets/stats` merges the ticket analytics and MTTR (which need no database) into the response, so they work even when `repo.stats()` is not ready; optional filter parameters; an audit events route |

## 1. `GET /api/tickets/stats` (role lead) - response `data`

```jsonc
{
  // Saved AI analyses (database: repo.stats)
  "total_results": 120,
  "outcome_counts": {"RECOMMEND": 72, "CLARIFY": 30, "ESCALATE": 12, "BLOCKED": 6},   // a missing key = 0
  "feedback_by_category": [{"category": "Network", "useful": 13, "incorrect": 5, "unresolved": 3}],

  // Historical incidents (tickets.csv / the ticket index: no database needed)
  "incidents": {"total": 1100, "resolved": 980, "pending": 120, "critical": 147},
  "by_priority": [{"priority": "P1", "count": 147}],          // P1..P4
  "by_service":  [{"service": "Corporate VPN", "count": 50}], // affected_service
  "by_category": [{"category": "Network", "count": 150}],

  // MTTR (tickets/mttr.py)
  "mttr_baseline": {
    "median_hours": 18.5, "mean_hours": 26.25, "tickets": 980,
    "by_priority": [{"priority": "P1", "median_hours": 4.2, "tickets": 140}],
    "by_service":  [{"service": "Corporate VPN", "median_hours": 12.1, "tickets": 44}],
    "trend": [{"month": "2026-06", "median_hours": 20.3, "resolved": 160}]          // oldest first
  },
  "mttr_assisted": {"median_hours": 12.4, "assumptions": ["...", "..."]}           // or null
}
```

Definitions the dashboard relies on:

- `incidents.resolved` = status `resolved` or `closed`; `pending` = `open` or `in_progress`;
  `critical` = priority `P1`. Each ticket counted once. Resolution rate is computed as `resolved / total`.
- Every count is a whole number >= 0. `resolved + pending` must not exceed `total`.
- All durations are **hours** (decimals allowed). The page formats them (min / h / d).
- `mttr_assisted` must carry at least one assumption. Without assumptions the page refuses to show it.
- Time saved and improvement are computed on the page from the two medians and are always labelled
  **estimated**.

## 2. `tickets/mttr.py` (Akhilesh) - rules the numbers must follow

`baseline_mttr(tickets) -> dict` (measured, from historical tickets):

1. Duration = `resolved_at - created_at`, in hours.
2. Count a ticket only if both timestamps parse and the duration is >= 0. Skip missing, unparseable or
   negative durations, and report how many were skipped (e.g. `skipped`).
3. One duration per `ticket_id` (no double counting if a ticket appears twice).
4. Median (headline) and mean; the same rules for each priority, each service and each month
   (`trend`, month of `resolved_at`, `YYYY-MM`, oldest first).
5. The ticket data is synthetic (docs/DATA-CARD.md): no business hours or holidays.

`estimate_assisted(results, assumptions) -> dict` (an **estimate**, never observed):

1. Inputs: the baseline, saved analyses and resolutions (`resolutions.resolved_at`) where they exist,
   and explicit assumptions, e.g. "analysts accept N% of recommendations", "an accepted fix saves M% of
   diagnosis time".
2. Return `median_hours` and the `assumptions` as plain sentences. The page shows them next to the number.
3. Never report it as measured (docs/DATA-CARD.md, docs/PLAN.md "Decided so far").

Tests to include: missing timestamps skipped, negative durations skipped, duplicates counted once,
median/mean on a known small set, per-priority and per-service grouping, an empty input.

## 3. Optional filters on `/stats` (Akshay)

`date_from`, `date_to`, `affected_service`, `priority` as query parameters (the same meaning as on
`GET /api/tickets`). The Dashboard does **not** filter on the page: `/api/tickets` returns at most 50 rows,
so counting from it would be wrong. Until `/stats` takes filters, the page shows a note instead of filters.

## 4. Audit activity (Akshay, `common/api.py`)

Live today and used by the Dashboard: `GET /api/audit/verify` (hash-chain check) and
`GET /api/audit/results` (recent saved results with their feedback count).

Missing for a full activity feed:

- `GET /api/audit/events?limit=&event_type=` over the existing `audit.list_events()`, returning
  `seq, event_type, result_id, case_id, ts` (no `data_json`: it can hold ticket text). This gives
  feedback events with their own time.
- **Resolution events do not exist** in this audit log (`EVENT_TYPES` = result, feedback). The planned
  `audit_events` table in Postgres (docs/ARCHITECTURE.md, Raswanth) covers resolutions; it needs a read
  route if the Dashboard should list them.
- Today the ticket pipeline saves through `db/repo.py`, not this audit log, so the log can be empty in a
  demo until both are connected.
