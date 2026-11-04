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

    # Gunicorn runs without --reload and debug is off, so without this, Jinja
    # compiles each template once per worker and doesn't reread the file:
    # a template edit needed a restart, though the docs said it didn't.
    app.config["TEMPLATES_AUTO_RELOAD"] = True


    # Every link to a static file carries the file's modification time, ?v=1793750123 (item 296).
    # Behind the Cloudflare tunnel a browser kept running an old script and stylesheet after they
    # changed, without asking the server, although it sends no-cache.  A changed file now has a new
    # address, so no copy of the old one can be served; an unchanged file keeps its address and
    # still caches.  A file that is not there gets no version, and Flask answers it with a 404.
    @app.url_defaults
    def _version_static_files(endpoint, values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            try:
                values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
            except (OSError, TypeError, ValueError):
                pass

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

    # The release this code is, for the footer (item 289).  Read once, at startup:  a deploy writes
    # hailsys/VERSION and then restarts web.  None (no file) shows as "development".
    from hailsys.version import read_version
    app_version = read_version()

    @app.context_processor
    def inject_version():
        return {"app_version": app_version}

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

    from . import emailtpl
    app.register_blueprint(emailtpl.emailtpl_bp)

    from . import send
    app.register_blueprint(send.send_bp)

    from . import drip
    app.register_blueprint(drip.drip_bp)

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
