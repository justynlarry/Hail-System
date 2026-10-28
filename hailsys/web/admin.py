import psycopg

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from hailsys.queries import quota, dncimport, ccstate, emailadmin, likelihoodadmin
from hailsys.db import get_connection
from hailsys.web.auth import MIN_PASSWORD_LENGTH, hash_password, require_role
from hailsys.tuning import DISPLAY_TZ, denver_day_bounds
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from hailsys.email import emailconfig

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

METRES_PER_MILE = 1609.344
HISTORY_LIMIT = 20
ROLES = ("admin", "sender", "viewer")
ZONE_NUMBER_FIELDS = likelihoodadmin.RADAR_KEYS + likelihoodadmin.LIKELIHOOD_KEYS

UNIQUE_MESSAGES = {
    "users_user_name_key": "That user name is already taken.",
    "users_emp_email_key": "That email address is already in use",
}

CHECK_MESSAGES = {
    "match_within_zip_radius":
        "Match radius can't be larger than the zip radius.",
    "settings_listing_freshness_days_check":
        "Listing freshness must be between 1 and 90 days",
    "settings_default_zip_radius_miles_check":
        "Zip radius must be more than 0 and no more than 10 miles.",
    "settings_default_match_radius_miles_check":
        "Match radius must be more than 0 and no more than 10 miles.",
    "settings_rentcast_billing_day_check":
        "Billing day must be between 1 and 28.",
    "settings_rentcast_monthly_quota_check":
        "Monthly quota must be more than 0.",
    "settings_match_max_age_days_check":
        "Storm age limit must be between 1 and 90 days.",
    "settings_email_cap_days_check":
        "Email cap must be between 1 and 90 days.",
    "settings_drip_max_followups_check":
        "Most follow-ups must be between 0 and 10.",
    "settings_drip_max_gap_days_check":
        "Longest gap must be between 1 and 30 days.",
}

SELF_ACTION_MESSAGE = (
    "You can't change your own account here. "
    "Ask another admin, or use the change-password page."
)

EMPTY_USER_FORM = {"user_name": "", "first_name": "", "last_name": "",
                   "email": "", "role": ""}


@admin_bp.before_request
def _admin_only():
    # before_request hook that returns a response ends request
    # and view never runs.  require_role returns None when the 
    # user is allowed.
    return require_role("admin")


def _zone_form_from(row):
    """A parameter set as the hail-zone form shows it:  numbers as text, the grid, a blank name
    (the admin has to say why, for the history)."""
    out = {key: str(row[key]) for key in ZONE_NUMBER_FIELDS}
    out["name"] = ""
    out["rules"] = dict(row["rules"])
    return out


def _zone_form_from_request():
    """The hail-zone form as typed.  The 20 levels arrive as rule_<layer>_<band>."""
    values = {key: request.form.get(key, "") for key in ZONE_NUMBER_FIELDS}
    values["name"] = request.form.get("name", "")
    values["rules"] = {
        (layer, band): request.form.get(f"rule_{layer}_{band}", "")
        for layer in likelihoodadmin.LAYERS for band in likelihoodadmin.BANDS}
    return values



def _render_admin(form=None, status=200, zone_form=None, trial=None, trial_day=None):
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
                   rentcast_billing_day, rentcast_monthly_quota,
                   global_sessions_invalidated_at,
                   hail_pair_ceiling_m() AS ceiling_m,
                   listing_freshness_days,
                   match_max_age_days, email_cap_days,
                   drip_max_followups, drip_max_gap_days,
                   contact_email, schedule_url, from_name, from_email, reply_to
            FROM settings
            """
        )
        settings = cur.fetchone()
        usage = quota.fetch_usage(
            conn,
            today=datetime.now(DISPLAY_TZ).date(),
            billing_day=settings["rentcast_billing_day"],
            quota=settings["rentcast_monthly_quota"],
            day_bounds=denver_day_bounds,
        )

        cur.execute(
            """
            SELECT h.changed_at, u.user_name,
                   h.default_zip_radius_miles, h.default_match_radius_miles,
                   h.rentcast_billing_day, h.rentcast_monthly_quota, h.listing_freshness_days,
                   h.match_max_age_days, h.email_cap_days,
                   h.drip_max_followups, h.drip_max_gap_days,
                   h.contact_email, h.schedule_url, h.from_name, h.from_email, h.reply_to
            FROM settings_history h
            JOIN users u ON u.emp_id = h.changed_by
            ORDER BY h.changed_at DESC
            LIMIT %s
            """,
            (HISTORY_LIMIT,),
        )
        history = cur.fetchall()
        cc = ccstate.fetch_state(conn)
        images = emailadmin.fetch_images(conn)

        zone_sets = likelihoodadmin.history(conn)
        unbuilt = likelihoodadmin.unbuilt_days(conn)
        storm_states = likelihoodadmin.storm_states(conn)
        observer_wording = likelihoodadmin.observer_wording_approved(conn)
        suggested_day = likelihoodadmin.suggest_day(conn)

    # "Use these values again" is a link, not a write, it fills the form from an older set and the 
    # admin reviews it, names it, and saves.
    if zone_form is None and zone_sets:
        wanted = request.args.get("use_set", type=int)
        zone_form = _zone_form_from(
            next((s for s in zone_sets if s["set_id"] == wanted), zone_sets[0]))


    return render_template(
        "admin.html",
        users=users,
        settings=settings,
        ceiling_miles=settings["ceiling_m"] / METRES_PER_MILE,
        history=history,
        display_tz=DISPLAY_TZ,
        form=form or EMPTY_USER_FORM,
        roles=ROLES,
        min_password_length=MIN_PASSWORD_LENGTH,
        usage=usage,
        cc=cc,
        images=images,
        required_images=emailconfig.REQUIRED_IMAGES,
        zone_sets=zone_sets,
        zone_form=zone_form,
        unbuilt=unbuilt,
        storm_states=storm_states,
        observer_wording=observer_wording,
        zone_layers=likelihoodadmin.LAYERS,
        zone_bands=likelihoodadmin.BANDS,
        zone_levels=likelihoodadmin.LEVELS,
        trial=trial,
        try_day=trial_day or suggested_day,
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
    if _refuse_self(emp_id):
        flash(SELF_ACTION_MESSAGE)
        return redirect(url_for("admin.index"))
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
    if _refuse_self(emp_id):
        flash(SELF_ACTION_MESSAGE)
        return redirect(url_for("admin.index"))
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
    if _refuse_self(emp_id):
        flash(SELF_ACTION_MESSAGE)
        return redirect(url_for("admin.index"))
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
        billing_day = int(request.form.get("billing_day") or "")
        monthly_quota = int(request.form.get("monthly_quota") or "")
        freshness_days = int(request.form.get("freshness_days") or "")
        match_max_age = int(request.form.get("match_max_age_days") or "")
        email_cap = int(request.form.get("email_cap_days") or "")
        drip_followups = int(request.form.get("drip_max_followups") or "")
        drip_gap = int(request.form.get("drip_max_gap_days") or "")
    except (InvalidOperation, ValueError):
        flash("Radii, billing day, and quota must all be numbers.")
        return redirect(url_for("admin.index"))
    
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_emp_id', %s, true)",
                (str(g.user["emp_id"]),),
            )
            cur.execute(
                "UPDATE settings SET default_zip_radius_miles = %s, "
                "       default_match_radius_miles = %s, "
                "        rentcast_billing_day = %s, "
                "         rentcast_monthly_quota = %s, " 
                "          listing_freshness_days = %s, "
                "           match_max_age_days = %s, "
                "            email_cap_days = %s, "
                "             drip_max_followups = %s, "
                "              drip_max_gap_days = %s "
                " WHERE id = 1",
                (zip_radius, match_radius, billing_day, monthly_quota, freshness_days, match_max_age,
                 email_cap, drip_followups, drip_gap),
            )
            conn.commit()
    except psycopg.errors.CheckViolation as e:
        flash(CHECK_MESSAGES.get(e.diag.constraint_name,
                                "Those values are not allowed."))
    # A number too big for its column (a quota past INTEGER's ~2.1 billion)
    # fails before any CHECK runs, as a DataError -- a different branch of
    # psycopg's tree from CheckViolation (IntegrityError), so it needs its
    # own clause or it's an uncaught 500.
    except psycopg.errors.DataError:
        flash("One of those numbers is too large to store.")
    else:
        flash(f"Settings updated.  Zip {zip_radius} mi, match {match_radius} mi. "
              f"Billing day {billing_day}, quota {monthly_quota}, "
              f"listings fresh for {freshness_days} days. "
              f"Campaigns: up to {drip_followups} follow-ups, gaps up to {drip_gap} days.")
    return redirect(url_for("admin.index"))


@admin_bp.route("/hail-zones", methods=["POST"])
def update_hail_zones():
    values = _zone_form_from_request()
    try:
        with get_connection() as conn:
            result = likelihoodadmin.save(conn, g.user["emp_id"], values)
            conn.commit()
    except likelihoodadmin.SetAdminError as exc:
        flash(str(exc))
        return _render_admin(zone_form=values, status=400)
    flash(f"Saved hail-zone set {result['set_id']}.  It is in use now.  The nightly job builds "
          "with it, and the map draws only days built with it.  Rebuild the earlier days "
          "(see the box in Hail zones).")
    return redirect(url_for("admin.index") + "#hail-zones")


@admin_bp.route("/hail-zones/try", methods=["POST"])
def try_hail_zones():
    values = _zone_form_from_request()
    try:
        day = date.fromisoformat(request.form.get("day", ""))
    except ValueError:
        flash("Pick a day to try it on.")
        return _render_admin(zone_form=values, status=400)
    try:
        with get_connection() as conn:
            trial = likelihoodadmin.try_day(conn, g.user["emp_id"], values, day)
    except likelihoodadmin.SetAdminError as exc:
        flash(str(exc))
        return _render_admin(zone_form=values, status=400, trial_day=day)
    return _render_admin(zone_form=values, trial=trial, trial_day=day)



@admin_bp.route("/hail-zones/states", methods=["POST"])
def update_storm_states():
    try:
        with get_connection() as conn:
            states = likelihoodadmin.save_storm_states(
                conn, g.user["emp_id"], request.form.get("storm_states", ""))
            conn.commit()
    except likelihoodadmin.SetAdminError as exc:
        flash(str(exc))
    else:
        flash(f"Storm states set to {states}.  It applies to warnings fetched from now on.")
    return redirect(url_for("admin.index") + "#hail-zones")


@admin_bp.route("/hail-zones/observer-wording", methods=["POST"])
def update_observer_wording():
    # An unticked checkbox sends nothing, so a missing 'approved' means not approved:
    # a malformed post can only switch it off, never on.
    approved = request.form.get("approved") == "1"
    with get_connection() as conn:
        likelihoodadmin.save_observer_wording(conn, g.user["emp_id"], approved)
        conn.commit()
    if approved:
        flash("Observer wording approved: zone emails may now rest on a CoCoRaHS report.")
    else:
        flash("Observer wording not approved:  observer matches are held back from zone sends.")
    return redirect(url_for("admin.index") + "#hail-zones")


def _email_action(write, ok_message):
    """Run one email write; a refused value is shown as a message, not an error page."""
    try:
        with get_connection() as conn:
            write(conn)
    except emailadmin.EmailAdminError as exc:
        flash(str(exc))
    else:
        flash(ok_message)
    return redirect(url_for("admin.index") + "#email")


@admin_bp.route("/email-settings", methods=["POST"])
def update_email_settings():
    f = request.form
    return _email_action(
        lambda conn: emailadmin.save_email_settings(
            conn, g.user["emp_id"], f.get("contact_email"), f.get("schedule_url"),
            f.get("from_name"), f.get("from_email"), f.get("reply_to")),
        "Email settings updated.")


@admin_bp.route("/images", methods=["POST"])
def add_image():
    f = request.form
    return _email_action(
        lambda conn: emailadmin.add_image(
            conn, g.user["emp_id"], f.get("name"), f.get("url"), f.get("alt_text")),
        "Image added.")


@admin_bp.route("/images/<int:image_id>", methods=["POST"])
def update_image(image_id):
    f = request.form
    return _email_action(
        lambda conn: emailadmin.update_image(
            conn, g.user["emp_id"], image_id, f.get("url"), f.get("alt_text")),
        "Image updated.")


@admin_bp.route("/images/<int:image_id>/retire", methods=["POST"])
def retire_image(image_id):
    return _email_action(
        lambda conn: emailadmin.set_image_active(conn, g.user["emp_id"], image_id, False),
        "Image retired.")


@admin_bp.route("/images/<int:image_id>/reactivate", methods=["POST"])
def reactivate_image(image_id):
    return _email_action(
        lambda conn: emailadmin.set_image_active(conn, g.user["emp_id"], image_id, True),
        "Image reactivated.")


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


def _refuse_self(emp_id):
    """True if this action targets the acting admin's own row."""
    return emp_id ==g.user["emp_id"]



@admin_bp.route("/dnc/upload", methods=["POST"])
def dnc_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Choose a .csv file first.")
        return redirect(url_for("admin.index"))

    rows, error = dncimport.parse_upload(upload.read())
    if error:
        flash(error)
        return redirect(url_for("admin.index"))
    if not rows:
        flash("That file has a header but no rows.")
        return redirect(url_for("admin.index"))

    with get_connection() as conn:
        token = dncimport.stage(
            conn, rows=rows, filename=upload.filename,
            uploaded_by=g.user["emp_id"])

    return redirect(url_for("admin.dnc_preview", token=token))


@admin_bp.route("/dnc/preview/<token>")
def dnc_preview(token):
    with get_connection() as conn:
        batch = dncimport.fetch_batch(conn, token)
        if batch is None:
            flash("That preview has expired or was already used.")
            return redirect(url_for("admin.index"))
        summary, sample, rejects = dncimport.fetch_preview(
            conn, batch["batch_id"]
        )

    return render_template("dnc_preview.html", batch=batch, summary=summary,
                           sample=sample, rejects=rejects,
                           display_tz=DISPLAY_TZ)


@admin_bp.route("/dnc/commit/<token>", methods=["POST"])
def dnc_commit(token):
    with get_connection() as conn:
        batch = dncimport.fetch_batch(conn, token)
        if batch is None:
            flash("That preview has expired or was already used.")
            return redirect(url_for("admin.index"))
        if batch["committed_at"] is not None:
            flash("That file was already imported.")
            return redirect(url_for("admin.index"))

        added = dncimport.commit_batch(
            conn, batch_id=batch["batch_id"], added_by=g.user["emp_id"],
            filename=batch["filename"])

    flash(f"Imported {added} new suppression"
          f"{'' if added == 1 else 's'} from {batch['filename']}.")
    return redirect(url_for("admin.index"))


@admin_bp.route("/dnc/discard/<token>", methods=["POST"])
def dnc_discard(token):
    with get_connection() as conn:
        batch = dncimport.fetch_batch(conn, token)
        if batch is not None and batch["committed_at"] is None:
            dncimport.discard_batch(conn, batch["batch_id"])
        flash("Upload discarded. Nothing was imported")
        return redirect(url_for("admin.index"))
