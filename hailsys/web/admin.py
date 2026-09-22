import psycopg

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from hailsys.db import get_connection
from hailsys.web.auth import MIN_PASSWORD_LENGTH, hash_password, require_role
from hailsys.tuning import DISPLAY_TZ

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

METRES_PER_MILE = 1609.344
HISTORY_LIMIT = 20
ROLES = ("admin", "sender", "viewer")

UNIQUE_MESSAGES = {
    "users_user_name_key": "That user name is already taken.",
    "users_emp_email_key": "That email address is already in use",
}



@admin_bp.before_request
def _admin_only():
    # before_request hook that returns a response ends request
    # and view never runs.  require_role returns None when the 
    # user is allowed.
    return require_role("admin")

def _render_admin(form=None, status=200):
    """Admin Page.  Failed POST re-renders with typed values in the
    form so admin doesn't retype everything, password fields are 
    never echoed back.
    """
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
        display_tz=DISPLAY_TZ,
        form=form or {},
        roles=ROLES,
        min_password_length=MIN_PASSWORD_LENGTH,
    ), status


@admin_bp.route("/")
def index():
    return _render_admin()


@admin_bp.route("/users", methods=["POST"])
def create_user():
    form = {
        "user_name": (request.form.get("user_name") or "").strip().lower(),
        "first_name": (request.form.get("first_name") or "").strip(),
        "last_name": (request.form.get("last_name") or "").strip(),
        "email": (request.form.get("email") or "").strip().lower(),
        "role": request.form.get("role") or "",
    }
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm") or ""

    # Browser checks (required, minlength), but anyone can send a 
    # POST without the form, so we need a server check.
    errors = []
    if not all(form[k] for k in ("user_name", "first_name", "last_name", "email")):
        errors.append("All fields are required.")
    if form["email"] and "@" not in form["email"]:
        errors.append("That doesn't look like an email address.")
    if form["role"] not in ROLES:
        errors.append("Please pick a role.")
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    elif password != confirm:
        errors.append("Passwords do not match.")

    if errors:
        for e in errors:
            flash(e)
        return _render_admin(form=form, status=400)

    # Hash prior to opening connection, scrypt is slow deliberately, and
    # no reason to hold database connection while running
    password_hash = hash_password(password)

    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (user_name, emp_fname, emp_lname, emp_email,
                                   password_hash, role, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (form["user_name"], form["first_name"], form["last_name"],
                form["email"], password_hash, form["role"], g.user["emp_id"]),
            )
            conn.commit()
    except psycopg.errors.UniqueViolation as e:
        flash(UNIQUE_MESSAGES.get(e.diag.constraint_name, "That user already exists."))
        return _render_admin(form=form, status=409)
    
    flash(f"Created {form['user_name']} ({form['role']}).")
    return redirect(url_for("admin.index"))

