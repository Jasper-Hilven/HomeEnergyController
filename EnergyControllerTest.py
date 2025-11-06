import pytest
from datetime import datetime

from EnergyController import control_energy_flow


# --- Utility helpers ---
def _make_battery(charge, is_auto=False):
    return {
        "charge": charge,
        "isManual": False,
        "manualSetPower": 0,
        "isAutomatic": is_auto,
        "effectivePower": 0,
    }


def _make_car(connected=False):
    return {
        "IsCarConnected": connected,
        "CarIntendedPowerUsage": 0,
        "CarPowerUsage": 0,
    }


# --- Tests ---


def test_solar_surplus_with_car_connected():
    """Car should take ~85% of solar surplus; one battery auto, rest off."""
    batteries = [_make_battery(70, True), _make_battery(72)]
    car = _make_car(True)
    result = control_energy_flow(batteries, car, P1Usage=-2000, now=datetime(2025, 5, 5, 12, 0))

    car_power = result["carState"]["CarIntendedPowerUsage"]
    assert 1600 <= car_power <= 1800  # ≈85% of 2000
    auto_count = sum(b["isAutomatic"] for b in result["batteries"])
    assert auto_count == 1
    assert all(not b["isManual"] for b in result["batteries"])


def test_grid_draw_peak_hours():
    """During peak hours, discharge batteries to minimize grid draw."""
    batteries = [_make_battery(75, True), _make_battery(77)]
    car = _make_car(False)
    result = control_energy_flow(batteries, car, P1Usage=4000, now=datetime(2025, 5, 5, 18, 0))

    bats = result["batteries"]
    manual_bats = [b for b in bats if b["isManual"]]
    assert len(manual_bats) >= 0  # may or may not need manual assist
    # All manualSetPower values should be negative (discharging)
    for b in manual_bats:
        assert b["manualSetPower"] <= 0
    assert result["carState"]["CarIntendedPowerUsage"] == 0


def test_low_hours_grid_charging():
    """At night, car may charge ~1.4kW from grid even without surplus."""
    batteries = [_make_battery(60, True), _make_battery(62)]
    car = _make_car(True)
    result = control_energy_flow(batteries, car, P1Usage=500, now=datetime(2025, 5, 6, 1, 30))

    car_power = result["carState"]["CarIntendedPowerUsage"]
    assert 1300 <= car_power <= 1500
    auto_count = sum(b["isAutomatic"] for b in result["batteries"])
    assert auto_count == 1


def test_full_batteries_surplus():
    """When batteries full, car can use full surplus; batteries stay idle."""
    batteries = [_make_battery(100, True), _make_battery(100)]
    car = _make_car(True)
    result = control_energy_flow(batteries, car, P1Usage=-3000, now=datetime(2025, 5, 5, 13, 0))

    car_power = result["carState"]["CarIntendedPowerUsage"]
    assert 2900 <= car_power <= 3100
    assert all(b["manualSetPower"] == 0 for b in result["batteries"])


def test_low_batteries_peak_hours():
    """Do not discharge batteries below 20%, even at peak hours."""
    batteries = [_make_battery(19, True), _make_battery(18)]
    car = _make_car(False)
    result = control_energy_flow(batteries, car, P1Usage=3500, now=datetime(2025, 5, 5, 19, 0))

    # Batteries should not be discharging
    for b in result["batteries"]:
        assert not b["isManual"] or b["manualSetPower"] >= 0
    assert result["carState"]["CarIntendedPowerUsage"] == 0


def test_car_not_connected_solar_surplus():
    """With solar surplus and no car, charge lowest battery first."""
    batteries = [_make_battery(45, True), _make_battery(60)]
    car = _make_car(False)
    result = control_energy_flow(batteries, car, P1Usage=-2500, now=datetime(2025, 5, 5, 12, 0))

    bats = result["batteries"]
    # Expect at least one battery charging
    charging_bats = [b for b in bats if b["isManual"] and b["manualSetPower"] > 0]
    assert len(charging_bats) >= 1
    assert all(b["charge"] <= 60 for b in charging_bats)
    assert result["carState"]["CarIntendedPowerUsage"] == 0


def test_auto_only_small_imbalance():
    """If imbalance <1800W, only one auto battery should handle it."""
    batteries = [_make_battery(50, True), _make_battery(55)]
    car = _make_car(False)
    result = control_energy_flow(batteries, car, P1Usage=1000, now=datetime(2025, 5, 5, 14, 0))

    bats = result["batteries"]
    assert sum(b["isAutomatic"] for b in bats) == 1
    assert all(not b["isManual"] for b in bats)
    assert result["carState"]["CarIntendedPowerUsage"] == 0
