"""The response envelope every endpoint uses, plus the generic audit endpoints.

    success: {"ok": true,  "data": ...,  "error": null}
    error:   {"ok": false, "data": null, "error": {"code": "...", "message": "..."}}

Raise ApiError(status, code, message) anywhere in a route; main.py turns it into the error envelope.
Problem-specific routes live in their own module (app/api/tickets.py, app/auth/router.py) and are added with
app.include_router(...). The audit endpoints here are the LEGACY local audit store (docs/ARCHITECTURE.md); the
permanent database and its audit chain are in app/db/.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common import audit
from app.common.redact import mask

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


# ---------- envelope and errors ----------

class ApiError(Exception):
    """An expected failure, turned into the error envelope by the handler in main.py."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": True, "data": data, "error": None})


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "data": None, "error": {"code": code, "message": message}})


def is_valid_request_id(value: str) -> bool:
    """A client-sent X-Request-ID is reused only if it is short and plain (no log injection)."""
    return bool(_REQUEST_ID_RE.match(value))


def request_id_of(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)


# ---------- audit endpoints ----------

class FeedbackRequest(BaseModel):
    """A reviewer verdict. 'annotate' needs a note. item_id, if given, names the part of the result
    the verdict is about; without it the verdict is about the whole result."""

    model_config = ConfigDict(extra="forbid")
    verdict: Literal["confirm", "reject", "annotate"]
    item_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    note: Optional[str] = Field(default=None, max_length=audit.MAX_NOTE_CHARS)

    @model_validator(mode="after")
    def _annotate_needs_note(self) -> "FeedbackRequest":
        if self.note is not None and not self.note.strip():
            self.note = None
        if self.verdict == "annotate" and self.note is None:
            raise ValueError("an annotate verdict needs a note")
        return self


def _db(request: Request):
    return request.app.state.audit_db_path


def _audit_call(fn, *args, **kwargs):
    """Run an audit function and turn its errors into API errors."""
    try:
        return fn(*args, **kwargs)
    except audit.AuditNotFoundError:
        raise ApiError(404, "RESULT_NOT_FOUND", "No saved result has this ID.") from None
    except audit.AuditValidationError as exc:
        raise ApiError(422, "VALIDATION_ERROR", mask(str(exc))) from None
    except audit.AuditError:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "The audit database is not available.") from None


audit_router = APIRouter(prefix="/api/audit", tags=["audit"])
ResultId = PathParam(pattern=ID_PATTERN)


@audit_router.get("/verify", summary="Recalculate every hash in the audit chain")
def verify(request: Request):
    return ok(_audit_call(audit.verify_chain, _db(request)))


@audit_router.get("/results", summary="Saved results, newest first")
def list_results(request: Request, case_id: Optional[str] = Query(default=None, pattern=ID_PATTERN),
                 limit: int = Query(default=50, ge=1, le=audit.MAX_LIMIT), offset: int = Query(default=0, ge=0)):
    return ok(_audit_call(audit.list_results, _db(request), case_id=case_id, limit=limit, offset=offset))


@audit_router.get("/results/{result_id}", summary="One saved result (masked)")
def get_result(request: Request, result_id: str = ResultId):
    found = _audit_call(audit.get_result, _db(request), result_id)
    if found is None:
        raise ApiError(404, "RESULT_NOT_FOUND", "No saved result has this ID.")
    return ok(found)


@audit_router.get("/results/{result_id}/feedback", summary="All feedback for one result, oldest first")
def get_feedback(request: Request, result_id: str = ResultId):
    return ok(_audit_call(audit.get_feedback, _db(request), result_id))


@audit_router.post("/results/{result_id}/feedback", status_code=201, summary="Add a reviewer verdict")
def add_feedback(request: Request, body: FeedbackRequest, result_id: str = ResultId):
    record = _audit_call(audit.record_feedback, _db(request), result_id, body.verdict, item_id=body.item_id,
                         note=body.note, request_id=request_id_of(request))
    return ok(record, status=201)
