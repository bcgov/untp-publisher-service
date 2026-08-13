from flask import Flask
from flask_cors import CORS
from flask_qrcode import QRcode
from flask_session import Session

from config import Config
import logging

from app.routes.errors import bp as errors_bp
from app.routes.main import bp as main_bp


def create_app(config_class=Config):
    logging.basicConfig(level=logging.DEBUG)
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app)
    QRcode(app)
    # Session(app)

    app.register_blueprint(errors_bp)
    app.register_blueprint(main_bp)

    return app
