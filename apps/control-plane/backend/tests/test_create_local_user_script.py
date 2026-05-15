import runpy
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "create_local_user.py"
)


def load_script_globals() -> dict[str, object]:
    return runpy.run_path(str(SCRIPT_PATH), run_name="create_local_user_test")


def test_create_local_user_script_creates_viewer(monkeypatch, db_session) -> None:
    script = load_script_globals()
    monkeypatch.setitem(script["main"].__globals__, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "sys.argv",
        [
            "create_local_user.py",
            "--username",
            "script-viewer1",
            "--role",
            "viewer",
            "--password",
            "viewer-pass-123",
        ],
    )

    exit_code = script["main"]()

    assert exit_code == 0


def test_create_local_user_script_rejects_short_password(
    monkeypatch,
    db_session,
    capsys,
) -> None:
    script = load_script_globals()
    monkeypatch.setitem(script["main"].__globals__, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "sys.argv",
        [
            "create_local_user.py",
            "--username",
            "script-viewer2",
            "--role",
            "viewer",
            "--password",
            "short",
        ],
    )

    exit_code = script["main"]()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "at least 10 characters" in captured.err
