import os

from flask import Flask


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["FLASK_SECRET_KEY"]

    # Imported inside the factory, not the module top.  Views import the
    # blueprint's own app context, importing the module level makes the
    # package unimportable without a configured app, breaking the tests.
    from . import views
    app.register_blueprint(views.bp)

    return app