import sys
from datetime import date

import app.cli as cli


def test_backfill_features_does_not_require_strategy_configuration(
    monkeypatch, capsys
) -> None:
    session = object()
    settings = object()
    captured = {}

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

    def backfill(
        actual_session,
        actual_settings,
        start_date,
        end_date,
        resume=False,
    ):
        captured.update(
            session=actual_session,
            settings=actual_settings,
            start_date=start_date,
            end_date=end_date,
            resume=resume,
        )
        return {"completed_sessions": 1}

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SessionLocal", SessionContext)
    monkeypatch.setattr(cli, "backfill_daily_features", backfill)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "app.cli",
            "backfill-features",
            "--start",
            "2025-07-23",
            "--end",
            "2026-07-20",
            "--resume",
        ],
    )

    cli.main()

    assert captured == {
        "session": session,
        "settings": settings,
        "start_date": date(2025, 7, 23),
        "end_date": date(2026, 7, 20),
        "resume": True,
    }
    assert "'completed_sessions': 1" in capsys.readouterr().out
