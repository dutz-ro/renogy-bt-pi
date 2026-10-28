from renogy_monitor.modbus.registers import parse_battery_info, parse_dc_charger


def test_native_battery_fixture_values() -> None:
    values = parse_battery_info((65524, 133, 1, 37190, 1, 41694))
    assert values["battery_current_a"] == -0.12
    assert values["battery_voltage_v"] == 13.3
    assert round(float(values["battery_soc_pct"]), 1) == 95.8


def test_dc_charger_fixture_values() -> None:
    values = parse_dc_charger(
        (100, 134, 0, 2571, 129, 0, 0, 0, 0, 0, 0, 134, 138, 100,
         0, 13, 0, 3, 0, 42, 0, 1432, 0, 156, 0, 58685, 0, 0, 12, 4116)
    )
    assert values["battery_soc_pct"] == 100
    assert values["battery_voltage_v"] == 13.4
    assert values["controller_temperature_c"] == 10
    assert values["battery_temperature_c"] == 11
