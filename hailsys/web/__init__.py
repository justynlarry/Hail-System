import os

from flask import Flask, g, render_template
from flask_wtf.csrf import CSRFError, CSRFProtect
from jinja2 import StrictUndefined

def create_app():
    from hailsys.logconfig import configure_logging
    configure_logging()

    from hailsys.web.jobs import sweep_stale_pulls
    sweep_stale_pulls()

    app = Flask(__name__)
    app.secret_key = os.environ["FLASK_SECRET_KEY"]

    app = Flask(__name__)
    app.secret_key = os.environ["FLASK_SECRET_KEY"]

    # Caps every request body.  Set app-wide, not in the view.
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 *1024

    # A missing template variable renders blank by default, which has
    # created incorrect pages.  StrictUndefined now raises instead, optional
    # values must be tested with 'is defined'.
    app.jinja_env.undefined = StrictUndefined

    # CSRF fails closed, any POST without a valid token is rejected with a
    # 400.  None ties token life to the session, 3600s default will
    # 400 a form left open for an hour.

    app.config["WTF_CSRF_TIME_LIMIT"] = None
    CSRFProtect(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        return render_template("csrf_error.html", reason=e.description), 400

    @app.errorhandler(413)
    def handle_too_large(e):
        return render_template("csrf_error.html",
                               reason="That upload is too large (limit 10 MB)."), 413

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

    from . import search
    app.register_blueprint(search.search_bp)

    from . import cc
    app.register_blueprint(cc.cc_bp)

    from . import send
    app.register_blueprint(send.send_bp)

    # Populates g.user on every request; login_required and role_required
    # both read it. Without this registration g.user is never set, and both
    # decorators raise AttributeError instead of redirecting to login.
    from .auth import load_current_user, require_login
    app.before_request(load_current_user)
    app.before_request(require_login)

    # One magnitude formatter for templates; map_points() calls the same
    # function for the GeoJSON, so the table and the map popup agree.
    from hailsys.formatting import magnitude
    app.add_template_filter(magnitude, "magnitude")

    return app
