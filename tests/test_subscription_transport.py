# Many, if not most of these tests rely on using a graphql subscriptions
# client.  "apollographql/subscriptions-transport-ws" is used here for testing
# the graphql subscriptions server implementation. In order to run these tests,
# "cd" to the "tests" directory and "npm install".  Make sure you have nodejs
# installed in your $PATH.

from functools import wraps
import copy
import json
import multiprocess
import os
import sys

from flask import Flask
from flask_graphql import GraphQLView
from flask_sockets import Sockets
from geventwebsocket import WebSocketServer
from promise import Promise
import fakeredis
import graphene
import pytest
import redis

from graphql_subscriptions import (RedisPubsub, SubscriptionManager,
                                   ApolloSubscriptionServer)

from graphql_subscriptions.subscription_transport_ws import (
    SUBSCRIPTION_START, SUBSCRIPTION_FAIL, SUBSCRIPTION_DATA, KEEPALIVE,
    SUBSCRIPTION_END)

if os.name == 'posix' and sys.version_info[0] < 3:
    import subprocess32 as subprocess
else:
    import subprocess

TEST_PORT = 5000
KEEP_ALIVE_TEST_PORT = TEST_PORT + 1
DELAYED_TEST_PORT = TEST_PORT + 2
RAW_TEST_PORT = TEST_PORT + 4
EVENTS_TEST_PORT = TEST_PORT + 5


class PickableMock():
    def __init__(self, return_value=None, side_effect=None, name=None):
        self._return_value = return_value
        self._side_effect = side_effect
        self.name = name
        self.called = False
        self.call_count = 0
        self.call_args = set()

    def __call__(mock_self, *args, **kwargs):
        mock_self.called = True
        mock_self.call_count += 1
        call_args = {repr(arg) for arg in args}
        call_kwargs = {repr(item) for item in kwargs}
        mock_self.call_args = call_args | call_kwargs | mock_self.call_args

        if mock_self._side_effect and mock_self._return_value:
            mock_self._side_effect(mock_self, *args, **kwargs)
            return mock_self._return_value
        elif mock_self._side_effect:
            return mock_self._side_effect(mock_self, *args, **kwargs)
        elif mock_self._return_value:
            return mock_self._return_value

    def assert_called_once(self):
        assert self.call_count == 1

    def assert_called_with(self, *args, **kwargs):
        call_args = {repr(json.loads(json.dumps(arg))) for arg in args}
        call_kwargs = {repr(json.loads(json.dumps(item))) for item in kwargs}
        all_call_args = call_args | call_kwargs
        assert all_call_args.issubset(self.call_args)


def promisify(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        def executor(resolve, reject):
            return resolve(f(*args, **kwargs))

        return Promise(executor)

    return wrapper


@pytest.fixture
def data():
    return {
        '1': {
            'id': '1',
            'name': 'Dan'
        },
        '2': {
            'id': '2',
            'name': 'Marie'
        },
        '3': {
            'id': '3',
            'name': 'Jessie'
        }
    }


@pytest.fixture
def pubsub(monkeypatch):
    monkeypatch.setattr(redis, 'StrictRedis', fakeredis.FakeStrictRedis)
    return RedisPubsub()


@pytest.fixture
def schema(data):
    class UserType(graphene.ObjectType):
        id = graphene.String()
        name = graphene.String()

    class Query(graphene.ObjectType):
        test_string = graphene.String()

    class Subscription(graphene.ObjectType):
        user = graphene.Field(UserType, id=graphene.String())
        user_filtered = graphene.Field(UserType, id=graphene.String())
        context = graphene.String()
        error = graphene.String()

        def resolve_user(self, args, context, info):
            id = args['id']
            name = data[args['id']]['name']
            return UserType(id=id, name=name)

        def resolve_user_filtered(self, args, context, info):
            id = args['id']
            name = data[args['id']]['name']
            return UserType(id=id, name=name)

        def resolve_context(self, args, context, info):
            return context

        def resolve_error(self, args, context, info):
            raise Exception('E1')

    return graphene.Schema(query=Query, subscription=Subscription)


@pytest.fixture
def sub_mgr(pubsub, schema):
    def user_filtered_func(**kwargs):
        args = kwargs.get('args')
        return {
            'user_filtered': {
                'filter': lambda root, ctx: root.get('id') == args.get('id')
            }
        }

    setup_funcs = {'user_filtered': user_filtered_func}

    return SubscriptionManager(schema, pubsub, setup_funcs)


@pytest.fixture
def handlers():
    def copy_and_update_dict(msg, params, websocket):
        new_params = copy.deepcopy(params)
        new_params.update({'context': msg['context']})
        return new_params

    return {'on_subscribe': promisify(copy_and_update_dict)}


@pytest.fixture
def options(handlers):
    return {
        'on_subscribe':
        lambda msg, params, ws: handlers['on_subscribe'](msg, params, ws)
    }


@pytest.fixture
def events_options(mocker):

    mgr = multiprocess.Manager()
    q = mgr.Queue()

    def on_subscribe(self, msg, params, websocket):
        new_params = copy.deepcopy(params)
        new_params.update({'context': msg.get('context', {})})
        q.put(self)
        return new_params

    def on_connect(self, message, websocket):
        q.put(self)

    def on_disconnect(self, websocket):
        q.put(self)

    def on_unsubscribe(self, websocket):
        q.put(self)

    events_options = {
        'on_subscribe':
        PickableMock(side_effect=promisify(on_subscribe), name='on_subscribe'),
        'on_unsubscribe':
        PickableMock(side_effect=on_unsubscribe, name='on_unsubscribe'),
        'on_connect':
        PickableMock(
            return_value={'test': 'test_context'},
            side_effect=on_connect,
            name='on_connect'),
        'on_disconnect':
        PickableMock(side_effect=on_disconnect, name='on_disconnect')
    }

    return events_options, q


def create_app(sub_mgr, schema, options):
    app = Flask(__name__)
    sockets = Sockets(app)

    app.app_protocol = lambda environ_path_info: 'graphql-subscriptions'

    app.add_url_rule(
        '/graphql',
        view_func=GraphQLView.as_view('graphql', schema=schema, graphiql=True))

    @sockets.route('/socket')
    def socket_channel(websocket):
        subscription_server = ApolloSubscriptionServer(sub_mgr, websocket,
                                                       **options)
        subscription_server.handle()
        return []

    return app


def app_worker(app, port):
    server = WebSocketServer(('', port), app)
    server.serve_forever()


@pytest.fixture()
def server(sub_mgr, schema, options):

    app = create_app(sub_mgr, schema, options)

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': TEST_PORT})
    process.start()
    yield
    process.terminate()


@pytest.fixture()
def server_with_keep_alive(sub_mgr, schema, options):

    options_with_keep_alive = options.copy()
    options_with_keep_alive.update({'keep_alive': 10})
    app = create_app(sub_mgr, schema, options_with_keep_alive)

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': KEEP_ALIVE_TEST_PORT})
    process.start()
    yield
    process.terminate()


@pytest.fixture()
def server_with_events(sub_mgr, schema, events_options):

    options, q = events_options
    app = create_app(sub_mgr, schema, options)

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': EVENTS_TEST_PORT})

    process.start()
    yield q
    process.terminate()


def test_raise_exception_when_create_server_and_no_sub_mgr():
    with pytest.raises(AssertionError):
        ApolloSubscriptionServer(None, None)


def test_should_trigger_on_connect_if_client_connect_valid(server_with_events):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        new SubscriptionClient('ws://localhost:{1}/socket')
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'),
        EVENTS_TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        ret_value = server_with_events.get_nowait()
        assert ret_value.name == 'on_connect'
        ret_value.assert_called_once()


def test_should_trigger_on_connect_with_correct_cxn_params(server_with_events):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const connectionParams = {{test: true}}
        new SubscriptionClient('ws://localhost:{1}/socket', {{
        connectionParams,
        }})
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'),
        EVENTS_TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        ret_value = server_with_events.get_nowait()
        assert ret_value.name == 'on_connect'
        ret_value.assert_called_once()
        ret_value.assert_called_with({'test': True})


def test_trigger_on_disconnect_when_client_disconnects(server_with_events):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.client.close()
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'),
        EVENTS_TEST_PORT)
    subprocess.check_output(['node', '-e', node_script])
    ret_value = server_with_events.get_nowait()
    assert ret_value.name == 'on_disconnect'
    ret_value.assert_called_once()


def test_should_call_unsubscribe_when_client_closes_cxn(server_with_events):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.subscribe({{
            query: `subscription useInfo($id: String) {{
            user(id: $id) {{
              id
              name
            }}
          }}`,
            operationName: 'useInfo',
            variables: {{
              id: 3,
            }},
          }}, function (error, result) {{
            // nothing
          }}
        )
        setTimeout(() => {{
            client.client.close()
        }}, 500)
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'),
        EVENTS_TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=1)
    except:
        while True:
            ret_value = server_with_events.get_nowait()
            if ret_value.name == 'on_unsubscribe':
                ret_value.assert_called_once()
                break


def test_should_trigger_on_subscribe_when_client_subscribes(
        server_with_events):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.subscribe({{
            query: `subscription useInfo($id: String) {{
            user(id: $id) {{
              id
              name
            }}
          }}`,
            operationName: 'useInfo',
            variables: {{
              id: 3,
            }},
          }}, function (error, result) {{
            // nothing
          }})
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'),
        EVENTS_TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        while True:
            ret_value = server_with_events.get_nowait()
            if ret_value.name == 'on_subscribe':
                ret_value.assert_called_once()
                break


def test_should_trigger_on_unsubscribe_when_client_unsubscribes(
        server_with_events):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        const subId = client.subscribe({{
            query: `subscription useInfo($id: String) {{
            user(id: $id) {{
              id
              name
            }}
          }}`,
            operationName: 'useInfo',
            variables: {{
              id: 3,
            }},
          }}, function (error, result) {{
            // nothing
          }})
        client.unsubscribe(subId)
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'),
        EVENTS_TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        while True:
            ret_value = server_with_events.get_nowait()
            if ret_value.name == 'on_unsubscribe':
                ret_value.assert_called_once()
                break
