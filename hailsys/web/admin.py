from flask import Blueprint, render_template

from hailsys.db import get_connection
from hailsys.web.auth import require_role

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

METRES_PER_MILE = 1609.344
HISTORY_LIMIT = 20


@admin_bp.before_request
def _admin_only():
    # before_request hook that returns a response ends request
    # and view never runs.  require_role returns None when the 
    # user is allowed.
    return require_role("admin")

@admin_bp.route("/")
def index():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT emp_id, user_name, emp_fname, emp_lname, emp_email,
                   role, is_active, last_login_at, created_at,
                   sessions_invalidated_at
            FROM users
            WHERE role <> 'system'
            ORDER BY is_active DESC, emp_lname, emp_fname
            """
        )
        users = cur.fetchall()

        cur.execute(
            """
            SELECT default_zip_radius_miles, default_match_radius_miles,
                   global_sessions_invalidated_at,
                   hail_pair_ceiling_m() AS ceiling_m
            FROM settings
            """
        )
        settings = cur.fetchone()

        cur.execute(
            """
            SELECT h.changed_at, u.user_name,
                   h.default_zip_radius_miles, h.default_match_radius_miles
            FROM settings_history h
            JOIN users u ON u.emp_id = h.changed_by
            ORDER BY h.changed_at DESC
            LIMIT %s
            """,
            (HISTORY_LIMIT,),
        )
        history = cur.fetchall()

    return render_template(
        "admin.html",
        users=users,
        settings=settings,
        ceiling_miles=settings["ceiling_m"] / METRES_PER_MILE,
        history=history,
    )
