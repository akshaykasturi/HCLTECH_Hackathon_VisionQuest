"""Hand-worked examples for app.analysis.stats."""
import math

import pytest

from app.analysis.stats import bootstrap_ci, brier, mcnemar, risk_coverage


def test_brier_matches_hand_worked_example():
    # (0.9-1)^2 + (0.6-0)^2 + (0.8-1)^2 = 0.01 + 0.36 + 0.04 = 0.41, over 3 items
    assert brier([0.9, 0.6, 0.8], [1, 0, 1]) == pytest.approx(0.41 / 3)


def test_brier_is_zero_for_a_perfect_model():
    assert brier([1.0, 0.0, 1.0], [True, False, True]) == 0.0


def test_brier_rejects_different_lengths():
    with pytest.raises(ValueError):
        brier([0.5, 0.5], [1])


def test_bootstrap_constant_values_give_a_zero_width_interval():
    assert bootstrap_ci([0.7] * 20) == pytest.approx((0.7, 0.7, 0.7))


def test_bootstrap_is_repeatable_with_the_same_seed():
    values = [0, 1, 1, 0, 1, 1, 1, 0, 1, 1]
    assert bootstrap_ci(values) == bootstrap_ci(values)


def test_bootstrap_point_is_the_mean_and_inside_the_interval():
    point, low, high = bootstrap_ci([0, 1, 1, 0, 1, 1, 1, 0, 1, 1])
    assert point == pytest.approx(0.7)
    assert low <= point <= high


def test_bootstrap_interval_for_half_right_out_of_100_is_about_40_to_60():
    # Standard error = sqrt(0.5 * 0.5 / 100) = 0.05, so 95% is about 0.5 +/- 0.098
    _, low, high = bootstrap_ci([0] * 50 + [1] * 50)
    assert 0.38 <= low <= 0.42
    assert 0.58 <= high <= 0.62


def test_bootstrap_rejects_empty_input():
    with pytest.raises(ValueError):
        bootstrap_ci([])


def _pairs(b: int, c: int, both_right: int = 3, both_wrong: int = 2) -> tuple[list[int], list[int]]:
    """Paired results with b items only A got right and c items only B got right."""
    a = [1] * b + [0] * c + [1] * both_right + [0] * both_wrong
    m = [0] * b + [1] * c + [1] * both_right + [0] * both_wrong
    return a, m


def test_mcnemar_uses_exact_test_for_few_disagreements():
    # n = 7 disagreements: p = 2 * (C(7,0) + C(7,1)) / 2^7 = 2 * 8 / 128 = 0.125
    result = mcnemar(*_pairs(b=1, c=6))
    assert (result["b"], result["c"], result["n"]) == (1, 6, 7)
    assert result["method"] == "exact_binomial"
    assert result["p_value"] == pytest.approx(0.125)


def test_mcnemar_uses_chi_square_with_continuity_for_many_disagreements():
    # (|30 - 10| - 1)^2 / 40 = 361 / 40 = 9.025; p = erfc(sqrt(9.025 / 2)) ~ 0.00266
    result = mcnemar(*_pairs(b=30, c=10))
    assert result["method"] == "chi2_continuity"
    assert result["statistic"] == pytest.approx(9.025)
    assert result["p_value"] == pytest.approx(math.erfc(math.sqrt(9.025 / 2)))
    assert result["p_value"] == pytest.approx(0.00266, abs=1e-5)


def test_mcnemar_gives_p_of_one_when_models_never_disagree():
    result = mcnemar([1, 0, 1], [1, 0, 1])
    assert result["n"] == 0
    assert result["p_value"] == 1.0


def test_mcnemar_rejects_different_lengths():
    with pytest.raises(ValueError):
        mcnemar([1, 0], [1])


def test_risk_coverage_matches_hand_worked_example():
    rows = risk_coverage([0.9, 0.8, 0.8, 0.3], [1, 1, 0, 0])
    assert [r["threshold"] for r in rows] == [0.9, 0.8, 0.3]
    assert [r["answered"] for r in rows] == [1, 3, 4]
    assert [r["coverage"] for r in rows] == pytest.approx([0.25, 0.75, 1.0])
    assert [r["accuracy"] for r in rows] == pytest.approx([1.0, 2 / 3, 0.5])
    assert [r["risk"] for r in rows] == pytest.approx([0.0, 1 / 3, 0.5])
