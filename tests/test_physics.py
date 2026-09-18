"""Physical invariants independent of the learning implementation."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest

from dqn_meent.config import PhysicsConfig
from dqn_meent.physics import ForwardSolver


@pytest.fixture
def config():
    return PhysicsConfig(n_cells=16, fourier_order=7, material="constant", silicon_n=3.551726470588235)


def test_empty_pattern_matches_single_interface_fresnel(config):
    result = ForwardSolver(config).evaluate(np.zeros(config.n_cells))
    expected_r = ((config.n_incident-config.n_exit)/(config.n_incident+config.n_exit))**2
    assert result.reflectance == pytest.approx(expected_r, abs=1e-12)
    assert result.transmittance == pytest.approx(1-expected_r, abs=1e-12)
    assert result.efficiency == 0


def test_uniform_silicon_matches_analytic_slab(config):
    result = ForwardSolver(config).evaluate(np.ones(config.n_cells))
    n0, n1, n2 = config.n_incident, config.silicon_n, config.n_exit
    r01, r12 = (n0-n1)/(n0+n1), (n1-n2)/(n1+n2)
    phase = np.exp(2j*2*np.pi*n1*config.thickness_nm/config.wavelength_nm)
    expected_r = abs((r01+r12*phase)/(1+r01*r12*phase))**2
    assert result.reflectance == pytest.approx(expected_r, abs=1e-11)
    assert result.transmittance == pytest.approx(1-expected_r, abs=1e-11)
    assert result.efficiency == 0
    assert sum(v for m, v in zip(result.orders, result.transmission_orders) if m != 0) == 0


def test_lossless_power_conservation(config):
    solver = ForwardSolver(config)
    for design in np.random.default_rng(32).integers(2, size=(5, config.n_cells)):
        result = solver.evaluate(design)
        assert result.reflectance + result.transmittance == pytest.approx(1, abs=1e-10)
        assert 0 <= result.efficiency <= result.transmittance
        assert result.absorption < 1e-10
        json.dumps(result.to_dict(), allow_nan=False)


def test_translation_preserves_powers_and_reflection_swaps_order_sign(config):
    solver = ForwardSolver(config)
    design = np.array([1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 0, 0, 1])
    original = solver.evaluate(design)
    translated = solver.evaluate(np.roll(design, 3))
    mirrored = solver.evaluate(design[::-1])
    np.testing.assert_allclose(translated.transmission_orders, original.transmission_orders, atol=1e-10)
    np.testing.assert_allclose(translated.reflection_orders, original.reflection_orders, atol=1e-10)
    np.testing.assert_allclose(mirrored.transmission_orders, original.transmission_orders[::-1], atol=1e-10)
    np.testing.assert_allclose(mirrored.reflection_orders, original.reflection_orders[::-1], atol=1e-10)
    assert original.efficiency == original.transmission_orders[config.fourier_order+1]


def test_positive_extinction_is_passive_not_gain(config):
    solver = ForwardSolver(replace(config, silicon_k=0.05))
    assert solver.silicon_index.imag < 0
    result = solver.evaluate(np.ones(config.n_cells))
    assert 0 < result.absorption < 1
    assert result.reflectance + result.transmittance < 1


def test_green_material_wavelength_units_and_sign(config):
    solver = ForwardSolver(replace(config, material="meent_green", wavelength_nm=1100))
    assert solver.silicon_index.real == pytest.approx(3.542, abs=1e-8)
    assert solver.silicon_index.imag == pytest.approx(-3.0637e-5, abs=1e-10)
    assert solver.evaluate(np.ones(config.n_cells)).absorption > 0
    with pytest.raises(ValueError, match="250 to 1450"):
        ForwardSolver(replace(config, material="meent_green", wavelength_nm=2000))


def test_published_98_4_percent_design_cross_validates_solver():
    # Published pattern is independent of this DQN implementation. Its provenance
    # and the original solver settings are recorded in docs/paper-mapping.md.
    pattern = np.load(Path(__file__).parent / "fixtures" / "wl1100_ang50_eff98.4.npy")
    design = ((pattern+1)/2).astype(np.uint8)
    config = PhysicsConfig(
        n_cells=64, wavelength_nm=1100, deflection_angle_deg=50,
        thickness_nm=325, n_incident=1.45, n_exit=1,
        material="constant", silicon_n=3.551726470588235, fourier_order=40,
    )
    result = ForwardSolver(config).evaluate(design)
    # Allow implementation/discretization differences from the rounded original
    # 98.4% label, while catching incidence, polarization and order-sign errors.
    assert result.efficiency == pytest.approx(0.984, abs=0.003)
    assert result.transmission_orders[config.fourier_order-1] < 0.01


def test_cache_lru_counts_actual_rcwa_calls(config):
    solver = ForwardSolver(replace(config, cache_size=1))
    zero, one = np.zeros(config.n_cells), np.ones(config.n_cells)
    first = solver.evaluate(zero)
    assert solver.evaluate(zero) is first
    solver.evaluate(one)
    solver.evaluate(zero)
    assert (solver.evaluations, solver.solver_calls, solver.cache_hits) == (4, 3, 1)


@pytest.mark.parametrize("bad", [np.zeros(15), np.zeros((1, 16)), np.full(16, 0.5), np.full(16, np.nan)])
def test_invalid_design_is_not_silently_cast_or_cached(config, bad):
    solver = ForwardSolver(config)
    with pytest.raises(ValueError):
        solver.evaluate(bad)
    assert solver.evaluations == solver.solver_calls == 0
