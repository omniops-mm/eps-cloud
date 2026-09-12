"""Exercise first boot, restart and failed initialization without a database."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_bootstrap_failure_and_restart(tmp_path):
    shell = shutil.which("bash")
    if not shell and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        shell = str(candidate) if candidate.exists() else None
    if not shell:
        pytest.skip("bash is required for bootstrap control-flow checks")
    source = Path(__file__).resolve().parents[1] / "deploy/helm/eps/files"
    for name in ("bootstrap-db.sh", "init-db.sh"):
        shutil.copyfile(source / name, tmp_path / name)
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, body in {
        "initdb": 'mkdir -p "$PGDATA"; printf "16\\n" > "$PGDATA/PG_VERSION"; cat >/dev/null',
        "postgres": 'echo "postgres (PostgreSQL) 16.15"',
        "pg_ctl": 'echo "pg_ctl $*" >> calls',
        "createdb": "echo createdb >> calls",
        "psql": "cat >/dev/null; echo psql >> calls; [ ! -f fail-role ]",
    }.items():
        path = commands / name
        path.write_text("#!/bin/sh\nset -eu\n" + body + "\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)
    command = 'export PATH="$PWD/bin:$PATH" PGDATA="$PWD/data"; /bin/sh ./bootstrap-db.sh'
    env = dict(
        os.environ,
        POSTGRES_USER="eps_admin",
        POSTGRES_DB="eps",
        POSTGRES_PASSWORD="test-only",
        EPS_DB_PASSWORD="test-only",
        EPS_EXPORTER_PASSWORD="test-only",
    )

    def run():
        return subprocess.run(
            [shell, "-c", command], cwd=tmp_path, env=env, capture_output=True, check=False
        )

    assert run().returncode == 0
    assert (tmp_path / "data/.eps-bootstrap-complete").exists()
    before = (tmp_path / "calls").read_bytes()
    assert run().returncode == 0
    assert (tmp_path / "calls").read_bytes() == before
    # Existing unmarked storage is never silently initialized again.
    (tmp_path / "data/.eps-bootstrap-complete").unlink()
    assert run().returncode != 0
    assert (tmp_path / "calls").read_bytes() == before
    # On a fresh volume a role failure must stop PostgreSQL and leave no marker.
    shutil.rmtree(tmp_path / "data")
    (tmp_path / "fail-role").touch()
    assert run().returncode != 0
    assert not (tmp_path / "data/.eps-bootstrap-complete").exists()
    assert (tmp_path / "calls").read_text().splitlines()[-1].endswith("-m fast -w stop")
