from cowork_agent.persistence.pool import control_plane_pool_kwargs


def test_control_plane_pool_avoids_per_checkout_health_pings() -> None:
    options = control_plane_pool_kwargs()

    assert options["min_size"] == 3
    assert options["max_size"] == 8
    assert options["check"] is None
    assert options["max_idle"] == 600.0
    assert options["max_lifetime"] == 3600.0
    assert options["kwargs"] == {"prepare_threshold": None}
