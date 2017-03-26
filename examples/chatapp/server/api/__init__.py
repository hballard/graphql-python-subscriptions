from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_sockets import Sockets
from graphql_subscriptions import RedisPubsub


SECRET_KEY = 'dev'
SQLALCHEMY_DATABASE_URI = 'sqlite:///../test.db'
SQLALCHEMY_TRACK_MODIFICATIONS = False

app = Flask(__name__)
app.config.from_object(__name__)

db = SQLAlchemy(app)
sockets = Sockets(app)
pubsub = RedisPubsub()

# Added this method to support subprotocols in gevent-websockets;
# need to figure out how to retrieve map of environ_path to
# supported subprotocols; for now just return needed subprotocol for this app
app.app_protocol = lambda environ_path_info: 'graphql-subscriptions'


# Added cors headers for testing locally
def add_cors_header(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PATCH, PUT,\
            DELETE, OPTIONS'
    return response


app.after_request(add_cors_header)

import api.views
