"""Independent model-convention checks, not a claim of market price accuracy."""
import math
import QuantLib as ql
import pytest

from options_lab.pricing import greeks, implied_vol, price


@pytest.mark.parametrize("kind,forward,strike,vol", [
    ("cso", -3.0, -2.0, 2.5), ("cso", 0.0, 0.0, 0.2), ("cso", 2.0, -1.0, 6.0),
    ("vanilla", 5.0, 6.0, .9), ("vanilla", 75.0, 75.0, .3), ("vanilla", 150.0, 90.0, .7),
])
@pytest.mark.parametrize("tau", [1 / 365, .25, 2.0])
@pytest.mark.parametrize("rate", [-.01, .05])
@pytest.mark.parametrize("right", ["call", "put"])
def test_independent_quantlib_reference_with_matching_conventions(kind, forward, strike, vol, tau, rate, right):
    option_type = ql.Option.Call if right == "call" else ql.Option.Put
    discount = math.exp(-rate * tau)
    reference = (ql.bachelierBlackFormula if kind == "cso" else ql.blackFormula)(
        option_type, strike, forward, vol * math.sqrt(tau), discount)
    # Engineering target: 0.01 tick for a 0.01 USD/bbl price tick.
    assert abs(price(kind, forward, strike, tau, vol, right, rate) - reference) <= .0001


@pytest.mark.parametrize("kind,forward,strike,vol", [("cso", -1.0, -.8, 3.0), ("vanilla", 75., 78., .35)])
@pytest.mark.parametrize("right", ["call", "put"])
def test_greeks_finite_difference_economic_units_and_half_step(kind, forward, strike, vol, right):
    tau, rate = .4, .035
    analytic = greeks(kind, forward, strike, tau, vol, right, rate)
    center = price(kind, forward, strike, tau, vol, right, rate)
    errors = []
    for scale in (1.0, .5):
        h = (0.001 if kind == "cso" else .01) * scale
        hv = (.001 if kind == "cso" else .0001) * scale
        up, down = (price(kind, forward + d, strike, tau, vol, right, rate) for d in (h, -h))
        delta, gamma = (up - down) / (2 * h), (up - 2 * center + down) / h ** 2
        vega = (price(kind, forward, strike, tau, vol + hv, right, rate)
                - price(kind, forward, strike, tau, vol - hv, right, rate)) / (2 * hv)
        ht = 1e-5 * scale
        theta = (price(kind, forward, strike, tau-ht, vol, right, rate)
                 - price(kind, forward, strike, tau+ht, vol, right, rate)) / (2*ht)
        # Derivative errors translated into one common economic bump's premium impact.
        comparisons = [(analytic["delta"], delta, .1), (analytic["gamma"], gamma, .5 * .1 ** 2),
                       (analytic["vega"], vega, .1 if kind == "cso" else .01),
                       (analytic["theta"], theta, 1/365)]
        for actual, reference, bump in comparisons:
            assert abs((actual - reference) * bump) <= max(.0001, .001 * abs(reference * bump))
        errors.append(abs(delta - analytic["delta"]) + abs(vega - analytic["vega"]) + abs(theta-analytic["theta"])/365)
    assert errors[1] <= errors[0] * .4 + 1e-8


@pytest.mark.parametrize("kind,forward,strike,vol", [("cso", -2., -2.5, 4.), ("vanilla", 70., 75., .4)])
def test_iv_round_trip_is_separately_labeled_numerical_consistency(kind, forward, strike, vol):
    observed = price(kind, forward, strike, .3, vol, "call", .03)
    solved = implied_vol(kind, forward, strike, .3, observed, "call", .03)
    assert solved == pytest.approx(vol, abs=1e-9)
    assert price(kind, forward, strike, .3, solved, "call", .03) == pytest.approx(observed, abs=1e-9)


@pytest.mark.parametrize("kind,forward,strike,vol", [("cso", -2., -3., 4.), ("vanilla", 70., 75., .4)])
def test_parity_limits_and_monotonicity(kind, forward, strike, vol):
    tau, rate = .2, .04
    call = price(kind, forward, strike, tau, vol, "call", rate)
    put = price(kind, forward, strike, tau, vol, "put", rate)
    assert call - put == pytest.approx(math.exp(-rate * tau) * (forward - strike), abs=1e-10)
    assert price(kind, forward, strike, tau, vol * 1.2, "call", rate) >= call
    assert price(kind, forward + .1, strike, tau, vol, "call", rate) >= call
    assert price(kind, forward, strike, 0., vol, "call", rate) == max(forward - strike, 0)
    assert price(kind, forward, strike, tau, 0., "call", rate) == pytest.approx(math.exp(-rate * tau) * max(forward - strike, 0))


def test_invalid_prices_and_domains_fail_instead_of_silently_switching_model():
    assert price("future", -37.63, None, 0., 0., None) == -37.63
    assert price("cso", -3., -2., .2, 1., "call") >= 0
    with pytest.raises(ValueError, match="positive"):
        price("vanilla", -1., 75., .2, .3, "call")
    with pytest.raises(ValueError, match="intrinsic"):
        implied_vol("cso", 2., 0., .2, 1., "call")
    with pytest.raises(ValueError, match="upper bound"):
        implied_vol("vanilla", 75., 75., .2, 75., "call")
    with pytest.raises(ValueError, match="not identifiable"):
        implied_vol("cso", 2., 0., 0., 2., "call")
    with pytest.raises(ValueError, match="finite"):
        price("cso", 1., 0., .2, float("nan"), "call")


def test_vega_units_are_not_interchangeable():
    normal = greeks("cso", 1., 1., .5, 2., "call")
    black = greeks("vanilla", 75., 75., .5, .3, "call")
    assert normal["vega_unit"] != black["vega_unit"]
    assert normal["vega_bump"] == .1
    assert black["vega_bump"] == .01


def test_boundary_greeks_label_nondifferentiability_and_one_sided_vega():
    for kind, forward, vol_bump in (("cso", -1., 1e-6), ("vanilla", 75., 1e-7)):
        result = greeks(kind, forward, forward, .2, 0., "call")
        reference = price(kind, forward, forward, .2, vol_bump, "call") / vol_bump
        assert result["vega"] == pytest.approx(reference, rel=1e-6)
        assert result["gamma"] is None
        assert result["boundary"] and "undefined" in result["boundary_note"]
    assert greeks("cso", 1., 1., 0., 2., "call")["theta"] is None
