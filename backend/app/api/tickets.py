"""Ticket routes (/api/tickets). Thin: validate, check the user, call the pipeline or the repository.

Every response uses the envelope from common/api.py: {ok, data, error}. Bad input -> 400 VALIDATION_ERROR or
BAD_ID (field names and reasons only, the submitted values are never echoed). No stack trace leaves the server.

  POST /analyze                        TicketIn -> AnalysisResult (pipeline.analyze)
  GET  /presets                        the 5 demo tickets for the Analyze page
  GET  ""                              search history (FR-07): TicketFilters as query params; q -> similarity search
  GET  /taxonomy                       distinct categories, subcategories per category, priorities, ...
  GET  /{ticket_id}                    one historical ticket (masked)
  POST /results/{result_id}/feedback   FeedbackIn            -> db/repo.add_feedback(result_id, user, feedback)
  POST /results/{result_id}/resolve                          -> db/repo.resolve(result_id, user) -> datetime
  GET  /history                                              -> db/repo.history(user=, limit=, offset=)
  GET  /stats (role lead)                                    -> db/repo.stats()

Login: app.auth.deps.current_user / require_role when auth is merged. Until then every request runs as
User("demo", "analyst") and a warning is logged once: the routes are open.
The repository (db/repo.py) is optional too: until it exists those routes answer 503 DB_NOT_READY.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Annotated, Any, Callable, Optional, TypeVar

from fastapi import APIRouter, Body, Depends, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError

from app.common.api import ID_PATTERN, ApiError, ok
from app.common.trace import optional
from app.tickets.models import FeedbackIn, TicketFilters, TicketIn, User
from app.tickets.pipeline import analyze
from app.tickets.similar import get_ticket_index, passes_filters, resolution_text, search_similar

logger = logging.getLogger(__name__)

PRESETS_FILE = Path(__file__).resolve().parents[1] / "tickets" / "presets.json"
LIST_FIELDS = ("ticket_id", "created_at", "status", "category", "subcategory", "priority", "assigned_team",
               "affected_service", "environment", "subject")
DETAIL_FIELDS = LIST_FIELDS + ("resolved_at", "channel", "description", "root_cause", "problem_id")
TAXONOMY_FIELDS = ("category", "priority", "status", "assigned_team", "affected_service", "environment")
SEARCH_K = 20
LIST_LIMIT = 50
_ID_RE = re.compile(ID_PATTERN)
M = TypeVar("M", bound=BaseModel)


# ---- Who is asking ---------------------------------------------------------------------------

def _demo_user() -> User:
    return User(username="demo", role="analyst")


_current_user: Optional[Callable[..., Any]] = optional("app.auth.deps", "current_user")
_require_role: Optional[Callable[..., Any]] = optional("app.auth.deps", "require_role")
AUTH_READY = _current_user is not None
if not AUTH_READY:
    logger.warning("auth not merged: routes are open")
    _current_user = _demo_user

AnyUser = Annotated[User, Depends(_current_user)]
LeadUser = Annotated[User, Depends(_require_role("lead") if AUTH_READY and _require_role else _current_user)]


# ---- Helpers -----------------------------------------------------------------------------------

def _check_id(value: str, what: str) -> str:
    if not _ID_RE.fullmatch(value or ""):
        raise ApiError(400, "BAD_ID", f"{what} must be 1-100 letters, digits, '.', '_' or '-'.")
    return value


def _parse(model: type[M], payload: Any) -> M:
    """Validate a JSON body; on error name the fields and reasons, never the submitted values."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        parts = [f"{'.'.join(str(p) for p in e.get('loc', ())) or 'body'}: {e.get('msg', 'invalid value')}"
                 for e in exc.errors()[:5]]
        raise ApiError(400, "VALIDATION_ERROR", "; ".join(parts)) from None


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Show the model in Swagger although the route validates the body itself (to answer 400, not 422)."""
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": model.model_json_schema()}}}}


def _pick(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: (row.get(f) or None) for f in fields}


def _repo(name: str) -> Callable[..., Any]:
    fn = optional("app.db.repo", name)
    if fn is None:
        raise ApiError(503, "DB_NOT_READY", "Saving is not available yet")
    return fn


def _repo_call(name: str, *args: Any, **kwargs: Any) -> Any:
    fn = _repo(name)
    try:
        return jsonable_encoder(fn(*args, **kwargs))
    except LookupError:
        raise ApiError(404, "RESULT_NOT_FOUND", "No saved result has this ID.") from None
    except Exception as exc:  # database down, bad state...: logged by class name, no stack trace out
        logger.error("repo.%s failed (%s)", name, exc.__class__.__name__)
        raise ApiError(503, "DATABASE_UNAVAILABLE", "The database is not available.") from None


# ---- Routes (fixed paths before /{ticket_id}) ----------------------------------------------------

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.post("/analyze", summary="Analyse a new ticket (steps 1-7)", openapi_extra=_body_schema(TicketIn))
def analyze_ticket(user: AnyUser, payload: Any = Body(...)):
    ticket = _parse(TicketIn, payload)
    try:
        result = analyze(ticket, user)
    except Exception as exc:  # e.g. masking failed: stop, say nothing about the content
        logger.error("analyze failed (%s)", exc.__class__.__name__)
        raise ApiError(500, "ANALYSIS_FAILED", "The ticket could not be analysed.") from None
    return ok(result.model_dump(mode="json"))


@router.get("/presets", summary="Demo tickets for the Analyze page")
def presets(user: AnyUser):
    items = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    return ok([{k: p.get(k) for k in ("id", "label", "expected_outcome", "ticket")} for p in items])


@router.get("", summary="Search historical tickets (FR-07)")
def search_tickets(user: AnyUser, filters: Annotated[TicketFilters, Query()]):
    index = get_ticket_index()
    if filters.q:
        rows = {str(r.get("ticket_id")): r for r in index.rows}
        hits = search_similar(filters.q, k=SEARCH_K, filters=filters, index=index)
        items = [{**_pick(rows.get(h.ticket_id, {"ticket_id": h.ticket_id}), LIST_FIELDS),
                  "score": h.score, "match": h.match} for h in hits]
    else:
        kept = [r for r in index.rows if passes_filters(r, filters)]
        kept.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)  # ISO dates sort as text
        items = [_pick(r, LIST_FIELDS) for r in kept[:LIST_LIMIT]]
    return ok(items)


@router.get("/taxonomy", summary="Categories, subcategories, priorities and other filter values")
def taxonomy(user: AnyUser):
    rows = get_ticket_index().rows
    values = {f: sorted({str(r[f]) for r in rows if r.get(f)}) for f in TAXONOMY_FIELDS}
    subcategories: dict[str, set[str]] = {}
    for r in rows:
        if r.get("category") and r.get("subcategory"):
            subcategories.setdefault(str(r["category"]), set()).add(str(r["subcategory"]))
    values["subcategory"] = {c: sorted(s) for c, s in sorted(subcategories.items())}
    return ok(values)


@router.get("/history", summary="Past results with their feedback")
def history(user: AnyUser, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    return ok(_repo_call("history", user=user, limit=limit, offset=offset))


@router.get("/stats", summary="Outcome mix, useful rate per category, MTTR (lead)")
def stats(user: LeadUser):
    return ok(_repo_call("stats"))


@router.post("/results/{result_id}/feedback", status_code=201, summary="Analyst feedback on a result",
             openapi_extra=_body_schema(FeedbackIn))
def add_feedback(result_id: str, user: AnyUser, payload: Any = Body(...)):
    _check_id(result_id, "result_id")
    _repo("add_feedback")  # 503 before 400 on the body: nothing can be saved anyway
    feedback = _parse(FeedbackIn, payload)
    return ok(_repo_call("add_feedback", result_id, user, feedback), status=201)


@router.post("/results/{result_id}/resolve", summary="Mark a result resolved (MTTR end time)")
def resolve(result_id: str, user: AnyUser):
    _check_id(result_id, "result_id")
    resolved_at = _repo_call("resolve", result_id, user)
    return ok({"result_id": result_id, "resolved_at": resolved_at})


@router.get("/{ticket_id}", summary="One historical ticket (masked)")
def get_ticket(ticket_id: str, user: AnyUser):
    _check_id(ticket_id, "ticket_id")
    row = next((r for r in get_ticket_index().rows if str(r.get("ticket_id")) == ticket_id), None)
    if row is None:
        raise ApiError(404, "TICKET_NOT_FOUND", "No historical ticket has this ID.")
    return ok({**_pick(row, DETAIL_FIELDS), "resolution_text": resolution_text(row)})
