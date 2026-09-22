from flask import Blueprint

from hailsys.web.auth import require_role

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
admin_bp.before_request(lambda: require_role("admin"))