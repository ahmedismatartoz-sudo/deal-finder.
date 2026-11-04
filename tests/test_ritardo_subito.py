"""5/11: misura di quanto presto leggiamo gli annunci nuovi di Subito."""


def test_ritardo_minuti():
    from dealfinder.pipeline.tempo_reale import ritardo_minuti
    assert ritardo_minuti("2026-11-05 00:05", "2026-11-05 00:12") == 7
    assert ritardo_minuti("2026-11-04 23:50", "2026-11-05 00:10") == 20
    assert ritardo_minuti(None, "2026-11-05 00:10") is None
    assert ritardo_minuti("2026-11-05 00:20", "2026-11-05 00:10") == 0
