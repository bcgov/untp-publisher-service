from flask import Blueprint, render_template
from werkzeug.exceptions import HTTPException

bp = Blueprint("errors", __name__)


@bp.app_errorhandler(HTTPException)
def handle_http_exception(error):
    return render_template("pages/errors/index.jinja", title="Error", error=error)


@bp.app_errorhandler(Exception)
def handle_exception(error):
    return render_template("pages/errors/index.jinja", title="Error", error=error)
