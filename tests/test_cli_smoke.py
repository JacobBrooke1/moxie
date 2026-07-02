"""End-to-end CLI smoke: the README quickstart must actually work — including
on Windows consoles whose encoding can't print a honey badger (cp1252)."""
import os
import subprocess
import sys


def _run(args, home, extra_env=None):
    env = dict(os.environ)
    env["MOXIE_HOME"] = str(home)
    # simulate the harshest console: strict cp1252, like piped cmd.exe
    env["PYTHONIOENCODING"] = "cp1252:strict"
    env.pop("MOXIE_API_KEY", None)
    env.pop("MOXIE_LIVE", None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "moxie", *args],
                          capture_output=True, text=True, env=env, timeout=120)


def test_quickstart_loop_survives_cp1252(tmp_path):
    home = tmp_path / "home"
    for args, expect in [
        (["init"], "initialized"),
        (["scan"], "Found"),
        (["review", "--yes"], "EXECUTED"),
        (["budget"], "left this month"),
        (["verify"], "intact"),
        (["doctor"], "Done."),
        (["skills"], "skill(s)"),
    ]:
        out = _run(args, home)
        assert out.returncode == 0, f"moxie {args[0]} failed:\n{out.stderr}"
        assert expect in out.stdout, f"moxie {args[0]}: {expect!r} not in output"


def test_kill_switch_round_trip(tmp_path):
    home = tmp_path / "home"
    _run(["init"], home)
    out = _run(["kill"], home)
    assert out.returncode == 0 and "ENGAGED" in out.stdout
    out = _run(["kill", "--release"], home)
    assert out.returncode == 0 and "released" in out.stdout
