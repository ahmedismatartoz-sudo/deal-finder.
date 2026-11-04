"""4/11: il cron "opportunità" superava le 12 ore e veniva fermato; ogni fase parte solo se c'è tempo."""


def test_fase_rimandata_se_tempo_finito(monkeypatch):
    from dealfinder.pipeline.collect import tempo_ok
    t0 = 1_000_000.0
    assert tempo_ok("supercar", 7, t0, ora=t0 + 3 * 3600)
    assert not tempo_ok("supercar", 7, t0, ora=t0 + 8 * 3600)
    monkeypatch.setenv("CRON_ORE_SUPERCAR", "9")
    assert tempo_ok("supercar", 7, t0, ora=t0 + 8 * 3600)


def test_ricalcolo_non_parte_tardi():
    from dealfinder.pipeline.collect import tempo_ok
    t0 = 0.0
    assert not tempo_ok("ricalcolo", 5, t0, ora=8 * 3600)   # 4/11: alle 8 ore partiva e sforava le 12
