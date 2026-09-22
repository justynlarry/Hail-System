import os

from flask import Flask
from . import admin


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["FLASK_SECRET_KEY"]

    # Imported inside the factory, not the module top.  Views import the
    # blueprint's own app context, importing the module level makes the
    # package unimportable without a configured app, breaking the tests.
    from . import views
    app.register_blueprint(views.bp)

    # Populates g.user on every request; login_required and role_required
    # both read it. Without this registration g.user is never set, and both
    # decorators raise AttributeError instead of redirecting to login.
    from .auth import load_current_user
    app.before_request(load_current_user)

    # One magnitude formatter for templates; map_points() calls the same
    # function for the GeoJSON, so the table and the map popup agree.
    from hailsys.formatting import magnitude
    app.add_template_filter(magnitude, "magnitude")

    app.register_blueprint(admin.admin_bp)

    return app