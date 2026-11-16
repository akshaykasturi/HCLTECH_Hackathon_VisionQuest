"""Two small helpers for a pipeline that teammates build in pieces.

Trace:     time each step, so the result can show "how this was decided" (step, ms, detail).
optional:  use a teammate's function if their module exists; otherwise None, so the pipeline
           carries on with a simple fallback and a note in the trace instead of waiting.

Example:
    trace = Trace()
    with trace.step("classify") as note:
        predict = optional("app.ml.classifier", "predict")
        if predict is None:
            note["detail"] = "classifier not ready: used the rule fallback"
            label = rule_fallback(item)
        else:
            label = predict(item)
    result["trace"] = trace.as_list()
"""
from __future__ import annotations

import importlib
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from pydantic import BaseModel


class TraceStep(BaseModel):
    step: str
    ms: float
    detail: str = ""


class Trace:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    @contextmanager
    def step(self, name: str) -> Iterator[dict[str, str]]:
        """Time a block; the block may set note["detail"]. The step is recorded even if the block raises."""
        note = {"detail": ""}
        start = time.perf_counter()
        try:
            yield note
        finally:
            ms = round((time.perf_counter() - start) * 1000, 2)
            self.steps.append(TraceStep(step=name, ms=ms, detail=note["detail"]))

    def as_list(self) -> list[dict[str, Any]]:
        return [s.model_dump() for s in self.steps]


def optional(module: str, name: str) -> Optional[Callable[..., Any]]:
    """A teammate's function if its module exists and defines it, else None."""
    try:
        return getattr(importlib.import_module(module), name, None)
    except ImportError:
        return None
