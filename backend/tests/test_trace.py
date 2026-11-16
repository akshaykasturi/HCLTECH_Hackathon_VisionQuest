"""Trace timer and optional imports (app/common/trace.py)."""
import pytest

from app.common.trace import Trace, optional


def test_steps_are_timed_in_order_with_details() -> None:
    trace = Trace()
    with trace.step("load") as note:
        note["detail"] = "3 items"
    with trace.step("rank"):
        pass
    steps = trace.as_list()
    assert [s["step"] for s in steps] == ["load", "rank"]
    assert steps[0]["detail"] == "3 items" and all(s["ms"] >= 0 for s in steps)


def test_a_failing_step_is_still_recorded() -> None:
    trace = Trace()
    with pytest.raises(ValueError):
        with trace.step("boom"):
            raise ValueError("x")
    assert [s.step for s in trace.steps] == ["boom"]


def test_optional_returns_the_function_or_none() -> None:
    assert optional("json", "dumps") is not None
    assert optional("json", "no_such_function") is None
    assert optional("app.not_built_yet.module", "predict") is None
