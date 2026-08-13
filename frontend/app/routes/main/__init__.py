from flask import Blueprint, render_template
from config import Config

bp = Blueprint("main", __name__)


@bp.route("/", methods=["GET"])
def index():
    return render_template("pages/main/index.jinja", title=Config.APP_NAME)
