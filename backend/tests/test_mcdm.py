"""Entropy weights, blending and TOPSIS (app/common/mcdm.py) on small matrices worked out by hand."""
import math

import numpy as np
import pytest

from app.common.mcdm import blend_weights, entropy_weights, order, topsis

# ---- TOPSIS: a 3 x 3 example by hand ----------------------------------------------------------------
# Criteria: c1 benefit, c2 benefit, c3 cost. Weights [0.5, 0.3, 0.2].
#   A = [3, 0, 1], B = [4, 3, 2], C = [0, 4, 2]
# Column norms: sqrt(9+16+0) = 5, sqrt(0+9+16) = 5, sqrt(1+4+4) = 3
#   r: A = [0.6, 0, 1/3]  B = [0.8, 0.6, 2/3]  C = [0, 0.8, 2/3]
#   v = r * w: A = [0.30, 0, 1/15]  B = [0.40, 0.18, 2/15]  C = [0, 0.24, 2/15]
# Best  (max, max, min) = [0.40, 0.24, 1/15];  worst (min, min, max) = [0, 0, 2/15]
#   A: d+ = sqrt(0.1^2 + 0.24^2 + 0)        = sqrt(0.0676)            = 0.26
#      d- = sqrt(0.3^2 + 0 + (1/15)^2)      = sqrt(0.09 + 1/225)
#   B: d+ = sqrt(0 + 0.06^2 + (1/15)^2)     = sqrt(0.0036 + 1/225)
#      d- = sqrt(0.4^2 + 0.18^2 + 0)        = sqrt(0.1924)
#   C: d+ = sqrt(0.4^2 + 0 + (1/15)^2)      = sqrt(0.16 + 1/225)
#      d- = sqrt(0 + 0.24^2 + 0)            = 0.24
X3 = [[3, 0, 1], [4, 3, 2], [0, 4, 2]]
W3 = [0.5, 0.3, 0.2]
B3 = [True, True, False]
D_PLUS = [0.26, math.sqrt(0.0036 + 1 / 225), math.sqrt(0.16 + 1 / 225)]
D_MINUS = [math.sqrt(0.09 + 1 / 225), math.sqrt(0.1924), 0.24]
CLOSENESS = [dm / (dp + dm) for dp, dm in zip(D_PLUS, D_MINUS)]   # ~ [0.541703, 0.830236, 0.371795]


def test_topsis_matches_the_hand_computed_3x3_example():
    r = topsis(X3, W3, B3)
    assert r.d_plus == pytest.approx(D_PLUS, abs=1e-6)
    assert r.d_minus == pytest.approx(D_MINUS, abs=1e-6)
    assert r.closeness == pytest.approx(CLOSENESS, abs=1e-6)
    assert list(order(r.closeness)) == [1, 0, 2]  # B, A, C


def test_contributions_are_shares_of_the_distance_from_the_worst_profile():
    r = topsis(X3, W3, B3)
    # A: (v - worst)^2 = [0.09, 0, 1/225], so c1 gives 0.09 / (0.09 + 1/225) of it.
    assert r.contributions[0] == pytest.approx([0.09 / (0.09 + 1 / 225), 0.0, (1 / 225) / (0.09 + 1 / 225)])
    assert (r.contributions >= 0).all()
    assert r.contributions.sum(axis=1) == pytest.approx([1.0, 1.0, 1.0])


def test_cost_criterion_lower_value_wins():
    # Same on the benefit criterion; the cost criterion decides: 2 beats 8.
    r = topsis([[5, 8], [5, 2]], [0.5, 0.5], [True, False])
    assert r.closeness[1] > r.closeness[0]
    assert list(order(r.closeness)) == [1, 0]


def test_topsis_one_and_zero_candidates_do_not_crash():
    one = topsis([[0.3, 0.7]], [0.5, 0.5], [True, False])
    assert one.closeness == pytest.approx([0.5])  # best = worst = itself
    assert one.contributions.tolist() == [[0.0, 0.0]]
    empty = topsis(np.zeros((0, 2)), [0.5, 0.5], [True, True])
    assert empty.closeness.size == 0 and empty.contributions.shape == (0, 2)


def test_topsis_zero_column_and_nan():
    r = topsis([[0, 1], [0, 3]], [0.5, 0.5])  # all-zero column: no division by zero
    assert np.isfinite(r.closeness).all()
    with pytest.raises(ValueError):
        topsis([[1, float("nan")], [2, 3]], [0.5, 0.5])


def test_ties_keep_the_original_retrieval_order():
    assert list(order([0.4, 0.7, 0.7, 0.4])) == [1, 2, 0, 3]
    assert list(order([0.5, 0.5], tie_rank=[2, 1])) == [1, 0]


# ---- Entropy weights ------------------------------------------------------------------------------------
# c1 = [0, 1, 2, 3] -> min-max [0, 1/3, 2/3, 1], sum 2 -> p = [0, 1/6, 1/3, 1/2]
#      e = -(1/6 ln 1/6 + 1/3 ln 1/3 + 1/2 ln 1/2) / ln 4 = 1.011404 / 1.386294 = 0.729574, d = 0.270426
# c2 = [1, 0, 0, 0] -> p = [1, 0, 0, 0], e = 0, d = 1
# c3 = [5, 5, 5, 5] -> constant, d = 0
# w = [0.270426, 1, 0] / 1.270426
_E1 = -(1 / 6 * math.log(1 / 6) + 1 / 3 * math.log(1 / 3) + 1 / 2 * math.log(1 / 2)) / math.log(4)
ENTROPY_W = [(1 - _E1) / (2 - _E1), 1 / (2 - _E1), 0.0]   # ~ [0.212862, 0.787138, 0]


def test_entropy_matches_hand_computed_weights_and_constant_column_gets_zero():
    w = entropy_weights([[0, 1, 5], [1, 0, 5], [2, 0, 5], [3, 0, 5]])
    assert w == pytest.approx(ENTROPY_W, abs=1e-6)
    assert w[2] == 0.0


def test_entropy_cost_column_weighs_the_same_as_its_mirror_image():
    # Cost [3, 2, 1, 0] -> (max - x) / range = [0, 1/3, 2/3, 1], same as benefit [0, 1, 2, 3] above.
    w = entropy_weights([[3, 1], [2, 0], [1, 0], [0, 0]], benefit=[False, True])
    assert w == pytest.approx(ENTROPY_W[:2], abs=1e-6)


def test_entropy_all_constant_columns_give_equal_weights():
    assert entropy_weights([[2, 0, 7], [2, 0, 7], [2, 0, 7]]) == pytest.approx([1 / 3] * 3)


def test_entropy_fewer_than_two_candidates_returns_none():
    assert entropy_weights([[0.2, 0.9]]) is None
    assert entropy_weights(np.zeros((0, 3))) is None


def test_entropy_nan_raises():
    with pytest.raises(ValueError):
        entropy_weights([[0.1, float("nan")], [0.2, 0.3]])


# ---- Blending -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lam", [0.0, 0.5, 1.0])
def test_blended_weights_sum_to_one_and_respect_the_floor(lam):
    w = blend_weights([0.9, 0.0, 0.1], [0.2, 0.5, 0.3], lam=lam, floor=0.05)
    assert w.sum() == pytest.approx(1.0)
    assert (w >= 0.05 - 1e-12).all()


def test_blend_by_hand():
    # lam 0.5: 0.5*[0.6, 0.4, 0] + 0.5*[0.2, 0.2, 0.6] = [0.4, 0.3, 0.3]; above the floor, unchanged.
    assert blend_weights([0.6, 0.4, 0.0], [0.2, 0.2, 0.6], lam=0.5, floor=0.05) == pytest.approx([0.4, 0.3, 0.3])
    # [0.9, 0, 0.1]: the 0 is lifted to 0.05; the other two share 0.95 in proportion 9:1 -> 0.855, 0.095.
    assert blend_weights([0.9, 0.0, 0.1], [0.9, 0.0, 0.1], lam=0.5, floor=0.05) == pytest.approx([0.855, 0.05, 0.095])


def test_blend_without_entropy_uses_the_prior():
    assert blend_weights(None, [2, 1, 1], lam=0.5, floor=0.0) == pytest.approx([0.5, 0.25, 0.25])


def test_blend_rejects_impossible_settings():
    with pytest.raises(ValueError):
        blend_weights(None, [0.5, 0.5], lam=0.5, floor=0.5)   # 2 x 0.5 = 1: no room left
    with pytest.raises(ValueError):
        blend_weights([0.5, 0.5], [0.5, 0.5], lam=1.5, floor=0.0)
