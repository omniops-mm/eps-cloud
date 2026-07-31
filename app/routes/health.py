"""Liveness and readiness. For Compose healthcheck and Kubernetes probes build on."""

from flask import Blueprint
from sqlalchemy import text

from app.db import db_session

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    return {"status": "ok"}


@bp.get("/readyz")
def readyz():
    try:
        db_session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        # any DB failure = not ready, reason goes to logs not the response
        return {"status": "unavailable"}, 503
    return {"status": "ready"}
