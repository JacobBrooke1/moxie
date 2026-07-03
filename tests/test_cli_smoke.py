"""End-to-end CLI smoke: the README quickstart must actually work — including
on Windows consoles whose encoding can't print a honey badger (cp1252)."""
import os
import subprocess
import sys
from types import SimpleNamespace


def _run(args, home, extra_env=None):
    env = dict(os.environ)
    env["MOXIE_HOME"] = str(home)
    # simulate the harshest console: strict cp1252, like piped cmd.exe
    env["PYTHONIOENCODING"] = "cp1252:strict"
    env.pop("MOXIE_API_KEY", None)
    env.pop("MOXIE_LIVE", None)
    env.update(extra_env or {})
    # Capture BYTES and decode with the encoding we told the child to write.
    # text=True would decode with the parent's locale — UTF-8 on Linux — and
    # explode on cp1252 high bytes like the bullet (0x95). That's a harness
    # artefact, not a product bug; decode explicitly instead.
    out = subprocess.run([sys.executable, "-m", "moxie", *args],
                         capture_output=True, env=env, timeout=120)
    return SimpleNamespace(
        returncode=out.returncode,
        stdout=out.stdout.decode("cp1252", errors="replace"),
        stderr=out.stderr.decode("cp1252", errors="replace"),
    )


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
