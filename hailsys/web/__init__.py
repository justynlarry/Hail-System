import os

from flask import flash, Flask, redirect, request, url_for, g, render_template
from flask_wtf.csrf import CSRFError, CSRFProtect

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["FLASK_SECRET_KEY"]

    # CSRF fails closed, any POST without a valid token is rejected with a
    # 400.  None ties token life to the session, 3600s default will
    # 400 a form left open for an hour.

    app.config["WTF_CSRF_TIME_LIMIT"] = None
    CSRFProtect(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        return render_template("csrf_error.html", reason=e.description), 400

    # Injected into every template render
    @app.context_processor
    def inject_permissions():
        user=getattr(g, "user", None)
        return {"can_pull": bool(user and user ["role"] in ("sender", "admin"))}

    # Both blueprints are imported inside the factory, not the module top.
    # They import the blueprint's own app context, so importing at module
    # level makes the package unimportable without a configured app,
    # breaking the tests.
    from . import views
    app.register_blueprint(views.bp)

    from . import admin
    app.register_blueprint(admin.admin_bp)

    # Populates g.user on every request; login_required and role_required
    # both read it. Without this registration g.user is never set, and both
    # decorators raise AttributeError instead of redirecting to login.
    from .auth import load_current_user
    app.before_request(load_current_user)

    # One magnitude formatter for templates; map_points() calls the same
    # function for the GeoJSON, so the table and the map popup agree.
    from hailsys.formatting import magnitude
    app.add_template_filter(magnitude, "magnitude")

    return app