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

    # One magnitude formatter for templates; map_points() calls the same
    # function for the GeoJSON, so the table and the map popup agree.
    from hailsys.formatting import magnitude
    app.add_template_filter(magnitude, "magnitude")

    return app