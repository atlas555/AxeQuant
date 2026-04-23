"""Tests for param_space parsing + size estimation."""

from __future__ import annotations


def test_parse_int_with_range_step():
    from app.services.backtestsys_plugin.api.param_space import parse_param_space
    specs = parse_param_space({"n": {"type": "int", "range": [10, 20], "step": 2}})
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "n" and s.bounds == (10.0, 20.0) and s.step == 2.0


def test_parse_float():
    from app.services.backtestsys_plugin.api.param_space import parse_param_space
    specs = parse_param_space({"m": {"type": "float", "range": [0.1, 0.5], "step": 0.05}})
    assert specs[0].name == "m"
    assert abs(specs[0].step - 0.05) < 1e-9


def test_parse_bool():
    from app.services.backtestsys_plugin.api.param_space import parse_param_space
    specs = parse_param_space({"flag": {"type": "bool", "default": True}})
    assert specs[0].default is True
    assert specs[0].options == [True, False]


def test_parse_choice():
    from app.services.backtestsys_plugin.api.param_space import parse_param_space
    specs = parse_param_space({"pick": {"type": "choice", "values": [1.0, 1.5, 2.0]}})
    assert specs[0].options == [1.0, 1.5, 2.0]


def test_parse_empty_raises():
    from app.services.backtestsys_plugin.api.param_space import parse_param_space
    import pytest
    with pytest.raises(ValueError):
        parse_param_space({})


def test_parse_unknown_type_raises():
    from app.services.backtestsys_plugin.api.param_space import parse_param_space
    import pytest
    with pytest.raises(ValueError):
        parse_param_space({"x": {"type": "unknown"}})


def test_size_estimate():
    from app.services.backtestsys_plugin.api.param_space import (
        parse_param_space, estimate_space_size,
    )
    specs = parse_param_space({
        "a": {"type": "int", "range": [0, 10], "step": 1},   # ~11
        "b": {"type": "bool"},                                # 2
        "c": {"type": "choice", "values": [1, 2, 3]},         # 3
    })
    # 11 * 2 * 3 = 66
    assert 60 <= estimate_space_size(specs) <= 70


def test_size_limit_raises():
    from app.services.backtestsys_plugin.api.param_space import (
        parse_param_space, check_size_or_raise,
    )
    huge = {
        f"p{i}": {"type": "int", "range": [0, 100], "step": 1}
        for i in range(4)
    }
    specs = parse_param_space(huge)
    import pytest
    with pytest.raises(ValueError):
        check_size_or_raise(specs, limit=1000)
