"""Exercise first boot, restart and failed initialization without a database."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _shell():
    shell = shutil.which("bash")
    if not shell and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        shell = str(candidate) if candidate.exists() else None
    if not shell:
        pytest.skip("bash is required for bootstrap control-flow checks")
    return shell


def test_bootstrap_failure_and_restart(tmp_path):
    shell = _shell()
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


def test_compose_exporter_credential_boundary(tmp_path):
    shell = _shell()
    source = Path(__file__).resolve().parents[1] / "observability/init-exporter-role.sh"
    shutil.copyfile(source, tmp_path / "init-exporter-role.sh")
    fake_psql = tmp_path / "psql"
    fake_psql.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@"\ncat\nexit "${PSQL_EXIT:-0}"\n',
        encoding="utf-8",
        newline="\n",
    )
    fake_psql.chmod(0o755)
    password = "synthetic-only'quoted;$(not-a-command)"
    env = dict(os.environ, POSTGRES_USER="eps", POSTGRES_DB="eps")

    def run(value, status="0"):
        return subprocess.run(
            [shell, "-c", 'export PATH="$PWD:$PATH"; /bin/sh ./init-exporter-role.sh'],
            cwd=tmp_path,
            env=dict(env, POSTGRES_EXPORTER_PASSWORD=value, PSQL_EXIT=status),
            capture_output=True,
            text=True,
            check=False,
        )

    result = run(password)
    assert result.returncode == 0
    assert password not in result.stdout + result.stderr
    assert "\\getenv exporter_password POSTGRES_EXPORTER_PASSWORD" in result.stdout
    assert "PASSWORD %L', :'exporter_password') \\gexec" in result.stdout
    assert result.stdout.index("SET log_min_error_statement") < result.stdout.index("CREATE ROLE")
    failed = run(password, "3")
    assert failed.returncode == 3
    assert password not in failed.stdout + failed.stderr
    missing = run("")
    assert missing.returncode != 0
    assert missing.stdout == ""
