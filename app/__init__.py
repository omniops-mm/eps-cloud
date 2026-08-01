"""Flask app factory. gunicorn serves this via "app:create_app()"."""

from flask import Flask, redirect, url_for
from werkzeug.wrappers import Response

from app import db
from app.config import get_settings
from app.routes import health, journal


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_settings().secret_key
    db.init_app(app)
    app.register_blueprint(health.bp)
    app.register_blueprint(journal.bp)

    @app.get("/")
    def index() -> Response:
        # the dashboard will live here; until then the journal is the front page
        return redirect(url_for("journal.today"))

    return app
