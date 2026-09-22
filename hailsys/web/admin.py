import psycopg

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from hailsys.db import get_connection
from hailsys.web.auth import MIN_PASSWORD_LENGTH, hash_password, require_role
from hailsys.tuning import DISPLAY_TZ
from decimal import Decimal, InvalidOperation


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

METRES_PER_MILE = 1609.344
HISTORY_LIMIT = 20
ROLES = ("admin", "sender", "viewer")

UNIQUE_MESSAGES = {
    "users_user_name_key": "That user name is already taken.",
    "users_emp_email_key": "That email address is already in use",
}

CHECK_MESSAGES = {
    "match_within_zip_radius":
        "Match radius can't be larger than the zip radius.",
    "settings_default_zip_radius_miles_check":
        "Zip radius must be more than 0 and no more than 10 miles.",
    "settings_default_match_radius_miles_check":
        "Match radius must be more than 0 and no more than 10 miles.",
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

def _user_action(sql, params, ok_message):
    """Run one user-table write and report result.
    """
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            affected = cur.rowcount
            conn.commit()
    except psycopg.errors.RaiseException as e:
        flash(e.diag.message_primary)
    else:
        flash(ok_message if affected else "No such user.")
    return redirect(url_for("admin.index"))


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


@admin_bp.route("/users/<int:emp_id>/deactivate", methods=["POST"])
def deactivate_user(emp_id):
    return _user_action(
        "UPDATE users SET is_active = FALSE "
        " WHERE emp_id = %s AND role <> 'system'",
        (emp_id,),
        "User Deactivated.",
    )


@admin_bp.route("/users/<int:emp_id>/reactivate", methods=["POST"])
def reactivate_user(emp_id):
    return _user_action(
        "UPDATE users SET is_active = TRUE "
        " WHERE emp_id = %s AND role <> 'system'",
        (emp_id,),
        "User Reactivated.",
    )

@admin_bp.route("/users/<int:emp_id>/role", methods=["POST"])
def change_role(emp_id):
    role = request.form.get("role") or ""
    if role not in ROLES:
        flash("Please pick a valid role.")
        return redirect(url_for("admin.index"))

    # sessions_invalidated_at: role is cached in the session
    # so without this, new role wouldn't apply until next
    # login, forcing re-login makes change instant
    return _user_action(
        "UPDATE users SET role = %s, sessions_invalidated_at = now() "
        " WHERE emp_id = %s AND role <> 'system'",
        (role, emp_id),
        f"(Role changed to {role}.  That user must sign in again.)"
    )

@admin_bp.route("/users/<int:emp_id>/boot", methods=["POST"])
def boot_user(emp_id):
    return _user_action(
        "UPDATE users SET sessions_invalidated_at = now()"
        " WHERE emp_id = %s AND role <> 'system'",
        (emp_id,),
        "User signed out, they can sign back in.",
    )

@admin_bp.route("/boot-all", methods=["POST"])
def boot_all():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE settings SET global_sessions_invalidated_at = now()")
        conn.commit()
    return redirect(url_for("main.login"))


@admin_bp.route("/settings", methods=["POST"])
def update_settings():
    try:
        zip_radius = Decimal(request.form.get("zip_radius") or "")
        match_radius = Decimal(request.form.get("match_radius") or "")
    except InvalidOperation:
        flash("Both radii must be numbers.")
        return redirect(url_for("admin.index"))
    
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_emp_id', %s, true)",
                (str(g.user["emp_id"]),),
            )
            cur.execute(
                "UPDATE settings SET default_zip_radius_miles = %s, "
                "       default_match_radius_miles = %s "
                " WHERE id = 1",
                (zip_radius, match_radius),
            )
            conn.commit()
    except psycopg.errors.CheckViolation as e:
        flash(CHECK_MESSAGES.get(e.diag.constraint_name,
                                "Those values are not allowed."))
    else:
        flash(f"Radii updated.  Zip {zip_radius} mi, match {match_radius} mi.")
    return redirect(url_for("admin.index"))


@admin_bp.route("/users/<int:emp_id>/password", methods=["POST"])
def reset_password(emp_id):
    if emp_id == g.user["emp_id"]:
        flash("Use the change-password page for your own account.")
        return redirect(url_for("admin.index"))
    
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""


    if len(new) < MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return redirect(url_for("admin.index"))
    if new != confirm:
        flash("Passwords do not match.")
        return redirect(url_for("admin.index"))

    return _user_action(
        "UPDATE users SET password_hash = %s, sessions_invalidated_at = now() "
        " WHERE emp_id = %s AND role <> 'system'",
        (hash_password(new), emp_id),
        "Password reset.  User has been signed out everywhere.",
    )