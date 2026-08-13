import os


class Config(object):
    APP_NAME = "Orgbook Publisher UI"

    SECRET_KEY = os.getenv("SECRET_KEY", "unsecured")
