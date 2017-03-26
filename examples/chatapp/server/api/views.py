from flask_graphql import GraphQLView
from graphql_subscriptions import ApolloSubscriptionServer

from . import app, sockets
from .schema import schema
from .subscriptions import subscription_mgr


app.add_url_rule(
    '/graphql',
    view_func=GraphQLView.as_view(
        'graphql',
        schema=schema,
        graphiql=True
    )
)


@sockets.route('/socket')
def socket_channel(websocket):
    subscription_server = ApolloSubscriptionServer(
        subscription_mgr,
        websocket,
        keep_alive=300
    )
    subscription_server.handle()
    return []
