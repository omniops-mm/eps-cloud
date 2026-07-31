"""Flask app factory. gunicorn serves this via "app:create_app()"."""

from flask import Flask

from app import db
from app.config import get_settings
from app.routes import health


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_settings().secret_key
    db.init_app(app)
    app.register_blueprint(health.bp)
    return app
