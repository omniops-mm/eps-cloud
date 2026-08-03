"""Flask app factory. gunicorn serves this via "app:create_app()"."""

from flask import Flask

from app import db, metrics
from app.config import get_settings
from app.logging import configure_logging
from app.routes import calendar, dashboard, health, journal, settings


def create_app() -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_settings().secret_key
    db.init_app(app)
    metrics.init_app(app)
    app.register_blueprint(health.bp)
    app.register_blueprint(journal.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(calendar.bp)
    app.register_blueprint(settings.bp)
    return app
