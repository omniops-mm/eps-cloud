"""Flask app factory. gunicorn serves this via "app:create_app()"."""

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from app import db, metrics, request_log
from app.config import get_settings
from app.logging import configure_logging
from app.routes import calendar, dashboard, health, journal, settings


def create_app() -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=get_settings().secret_key,
        MAX_CONTENT_LENGTH=1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=get_settings().secure_cookies,
    )
    db.init_app(app)
    metrics.init_app(app)
    request_log.init_app(app)
    CSRFProtect(app)
    app.register_blueprint(health.bp)
    app.register_blueprint(journal.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(settings.bp)
    return app
