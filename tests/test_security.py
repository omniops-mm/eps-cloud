"""Browser mutations require a session-bound token and a bounded request body."""

import re

import pytest
from flask.testing import FlaskClient
from pydantic import ValidationError

from app.config import Settings


def test_configuration_errors_omit_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    marker = "private-config-marker"
    with pytest.raises(ValidationError) as failure:
        Settings(database_url=marker, _env_file=None)  # type: ignore[call-arg]
    assert marker not in str(failure.value)


def test_csrf_rejects_missing_invalid_and_other_session_tokens(client: FlaskClient) -> None:
    path = "/settings/grace"
    assert (
        client.post(
            path, include_csrf=False, headers={"Origin": "https://untrusted.invalid"}
        ).status_code
        == 400
    )
    assert client.post(path, headers={"X-CSRFToken": "invalid"}).status_code == 400
    other = client.application.test_client()
    foreign_token = other.get("/_test/csrf").get_data(as_text=True)
    assert client.post(path, headers={"X-CSRFToken": foreign_token}).status_code == 400
    assert client.post(path, headers={"HX-Request": "true"}).status_code == 200


def test_plain_form_and_inherited_htmx_token(client: FlaskClient) -> None:
    page = client.get("/settings/").get_data(as_text=True)
    assert "X-CSRFToken" in page
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match is not None
    response = client.post(
        "/settings/timezone",
        data={"timezone": "Europe/Berlin", "csrf_token": match[1]},
        include_csrf=False,
    )
    assert response.status_code in {200, 302, 303}


def test_oversized_form_rejected(client: FlaskClient) -> None:
    assert client.post("/settings/grace", data={"padding": "x" * (1024 * 1024)}).status_code == 413


def test_invalid_task_kind_is_rejected_before_database_write(client: FlaskClient) -> None:
    assert (
        client.post(
            "/tasks/add",
            data={"name": "private-test-marker", "date": "2026-09-12", "kind": "invalid"},
        ).status_code
        == 400
    )
