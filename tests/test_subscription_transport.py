# Many, if not most of these tests rely on using a graphql subscriptions
# client.  "apollographql/subscriptions-transport-ws" is used here for testing
# the graphql subscriptions server implementation. In order to run these tests,
# "cd" to the "tests" directory and "npm install".  Make sure you have nodejs
# installed in your $PATH.

from future import standard_library
standard_library.install_aliases()
from builtins import object
from functools import wraps
import copy
import json
import os
import sys
import threading
import time

from flask import Flask, request, jsonify
from flask_graphql import GraphQLView
from flask_sockets import Sockets
from geventwebsocket import WebSocketServer
from promise import Promise
import queue
import fakeredis
import graphene
import multiprocess
import pytest
import redis
import requests

from graphql_subscriptions import (RedisPubsub, SubscriptionManager,
                                   SubscriptionServer)

from graphql_subscriptions.subscription_transport_ws import (SUBSCRIPTION_FAIL,
                                                             SUBSCRIPTION_DATA)

if os.name == 'posix' and sys.version_info[0] < 3:
    import subprocess32 as subprocess
else:
    import subprocess

TEST_PORT = 5000


class PickableMock(object):
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

    def assert_called_with_contains(self, arg_fragment):
        assert any([arg_fragment in item for item in self.call_args])


def promisify(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        def executor(resolve, reject):
            return resolve(f(*args, **kwargs))

        return Promise(executor)

    return wrapper


def enqueue_output(out, queue):
    with out:
        for line in iter(out.readline, b''):
            queue.put(line)


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
    def user_filtered(**kwargs):
        args = kwargs.get('args')
        return {
            'user_filtered': {
                'filter': lambda root, ctx: root.get('id') == args.get('id')
            }
        }

    setup_funcs = {'user_filtered': user_filtered}

    return SubscriptionManager(schema, pubsub, setup_funcs)


@pytest.fixture
def on_sub_handler():
    def context_handler():
        raise Exception('bad')

    def on_subscribe(msg, params, websocket):
        new_params = copy.deepcopy(params)
        new_params.update({'context': context_handler})
        return new_params

    return {'on_subscribe': promisify(on_subscribe)}


@pytest.fixture
def on_sub_mock(mocker):

    mgr = multiprocess.Manager()
    q = mgr.Queue()

    def on_subscribe(self, msg, params, websocket):
        new_params = copy.deepcopy(params)
        new_params.update({'context': msg.get('context', {})})
        q.put(self)
        return new_params

    on_sub_mock = {
        'on_subscribe':
        PickableMock(side_effect=promisify(on_subscribe), name='on_subscribe')
    }

    return on_sub_mock, q


@pytest.fixture
def options_mocks(mocker):

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

    options_mocks = {
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

    return options_mocks, q


def create_app(sub_mgr, schema, options):
    app = Flask(__name__)
    sockets = Sockets(app)

    app.app_protocol = lambda environ_path_info: 'graphql-subscriptions'

    app.add_url_rule(
        '/graphql',
        view_func=GraphQLView.as_view('graphql', schema=schema, graphiql=True))

    @app.route('/publish', methods=['POST'])
    def sub_mgr_publish():
        sub_mgr.publish(*request.get_json())
        return jsonify(request.get_json())

    @sockets.route('/socket')
    def socket_channel(websocket):
        subscription_server = SubscriptionServer(sub_mgr, websocket, **options)
        subscription_server.handle()
        return []

    return app


def app_worker(app, port):
    server = WebSocketServer(('', port), app)
    server.serve_forever()


@pytest.fixture()
def server(sub_mgr, schema, on_sub_mock):

    options, q = on_sub_mock
    app = create_app(sub_mgr, schema, options)

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': TEST_PORT})
    process.start()
    yield q
    process.terminate()


@pytest.fixture()
def server_with_mocks(sub_mgr, schema, options_mocks):

    options, q = options_mocks
    app = create_app(sub_mgr, schema, options)

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': TEST_PORT})

    process.start()
    yield q
    process.terminate()


@pytest.fixture()
def server_with_on_sub_handler(sub_mgr, schema, on_sub_handler):

    app = create_app(sub_mgr, schema, on_sub_handler)

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': TEST_PORT})
    process.start()
    yield
    process.terminate()


@pytest.fixture()
def server_with_keep_alive(sub_mgr, schema):

    app = create_app(sub_mgr, schema, {'keep_alive': .250})

    process = multiprocess.Process(
        target=app_worker, kwargs={'app': app,
                                   'port': TEST_PORT})
    process.start()
    yield
    process.terminate()


def test_raise_exception_when_create_server_and_no_sub_mgr():
    with pytest.raises(AssertionError):
        SubscriptionServer(None, None)


def test_should_trigger_on_connect_if_client_connect_valid(server_with_mocks):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        new SubscriptionClient('ws://localhost:{1}/socket')
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        mock = server_with_mocks.get_nowait()
        assert mock.name == 'on_connect'
        mock.assert_called_once()


def test_should_trigger_on_connect_with_correct_cxn_params(server_with_mocks):
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
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        mock = server_with_mocks.get_nowait()
        assert mock.name == 'on_connect'
        mock.assert_called_once()
        mock.assert_called_with({'test': True})


def test_trigger_on_disconnect_when_client_disconnects(server_with_mocks):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.client.close()
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)
    subprocess.check_output(['node', '-e', node_script])
    mock = server_with_mocks.get_nowait()
    assert mock.name == 'on_disconnect'
    mock.assert_called_once()


def test_should_call_unsubscribe_when_client_closes_cxn(server_with_mocks):
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
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=1)
    except:
        while True:
            mock = server_with_mocks.get_nowait()
            if mock.name == 'on_unsubscribe':
                mock.assert_called_once()
                break


def test_should_trigger_on_subscribe_when_client_subscribes(server_with_mocks):
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
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        while True:
            mock = server_with_mocks.get_nowait()
            if mock.name == 'on_subscribe':
                mock.assert_called_once()
                break


def test_should_trigger_on_unsubscribe_when_client_unsubscribes(
        server_with_mocks):
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
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)
    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        while True:
            mock = server_with_mocks.get_nowait()
            if mock.name == 'on_unsubscribe':
                mock.assert_called_once()
                break


def test_should_send_correct_results_to_multiple_client_subscriptions(server):

    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        const client1 = new SubscriptionClient('ws://localhost:{1}/socket')
        let numResults = 0;
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
                if (error) {{
                  console.log(JSON.stringify(error));
                }}
                if (result) {{
                  numResults++;
                  console.log(JSON.stringify({{
                    client: {{
                      result: result,
                      numResults: numResults
                    }}
                  }}));
                }} else {{
                  // pass
                }}
            }}
        );
        const client2 = new SubscriptionClient('ws://localhost:{1}/socket')
        let numResults1 = 0;
        client2.subscribe({{
            query: `subscription useInfo($id: String) {{
              user(id: $id) {{
                id
                name
              }}
            }}`,
            operationName: 'useInfo',
            variables: {{
              id: 2,
            }},

            }}, function (error, result) {{
                if (error) {{
                  console.log(JSON.stringify(error));
                }}
                if (result) {{
                  numResults1++;
                  console.log(JSON.stringify({{
                    client2: {{
                      result: result,
                      numResults: numResults1
                    }}
                  }}));
                }}
            }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(.2)
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT), json=['user', {}])
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    client = ret_values['client']
    assert client['result']['user']
    assert client['result']['user']['id'] == '3'
    assert client['result']['user']['name'] == 'Jessie'
    assert client['numResults'] == 1
    client2 = ret_values['client2']
    assert client2['result']['user']
    assert client2['result']['user']['id'] == '2'
    assert client2['result']['user']['name'] == 'Marie'
    assert client2['numResults'] == 1


# TODO: Graphene subscriptions implementation does not currently return an
# error for missing or incorrect field(s); this test will continue to fail
# until that is fixed
def test_send_subscription_fail_message_to_client_with_invalid_query(server):

    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        setTimeout(function () {{
            client.subscribe({{
                query: `subscription useInfo($id: String) {{
                  user(id: $id) {{
                    id
                    birthday
                  }}
                }}`,
                operationName: 'useInfo',
                variables: {{
                  id: 3,
                }},

                }}, function (error, result) {{
                }}
            );
        }}, 100);
            client.client.onmessage = (message) => {{
                let msg = JSON.parse(message.data)
                console.log(JSON.stringify({{[msg.type]: msg}}))
            }};
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(.2)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    assert ret_values['type'] == SUBSCRIPTION_FAIL
    assert len(ret_values['payload']['errors']) > 0


# TODO: troubleshoot this a bit...passes, but receives extra messages which I'm
# filtering out w/ the "AttributeError" exception clause
def test_should_setup_the_proper_filters_when_subscribing(server):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        const client2 = new SubscriptionClient('ws://localhost:{1}/socket')
        let numResults = 0;
        client.subscribe({{
            query: `subscription useInfoFilter1($id: String) {{
              userFiltered(id: $id) {{
                id
                name
              }}
            }}`,
            operationName: 'useInfoFilter1',
            variables: {{
              id: 3,
            }},

            }}, function (error, result) {{
                if (error) {{
                  console.log(JSON.stringify(error));
                }}
                if (result) {{
                  numResults += 1;
                  console.log(JSON.stringify({{
                    client: {{
                      result: result,
                      numResults: numResults
                    }}
                  }}));
                }} else {{
                  // pass
                }}
            }}
        );
        client2.subscribe({{
            query: `subscription useInfoFilter1($id: String) {{
              userFiltered(id: $id) {{
                id
                name
              }}
            }}`,
            operationName: 'useInfoFilter1',
            variables: {{
              id: 1,
            }},

            }}, function (error, result) {{
                if (error) {{
                  console.log(JSON.stringify(error));
                }}
                if (result) {{
                  numResults += 1;
                  console.log(JSON.stringify({{
                    client2: {{
                      result: result,
                      numResults: numResults
                    }}
                  }}));
                }}
            }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(.2)
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT),
        json=['user_filtered', {
            'id': 1
        }])
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT),
        json=['user_filtered', {
            'id': 2
        }])
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT),
        json=['user_filtered', {
            'id': 3
        }])
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except AttributeError:
            pass
        except queue.Empty:
            break
    client = ret_values['client']
    assert client['result']['userFiltered']
    assert client['result']['userFiltered']['id'] == '3'
    assert client['result']['userFiltered']['name'] == 'Jessie'
    assert client['numResults'] == 2
    client2 = ret_values['client2']
    assert client2['result']['userFiltered']
    assert client2['result']['userFiltered']['id'] == '1'
    assert client2['result']['userFiltered']['name'] == 'Dan'
    assert client2['numResults'] == 1


def test_correctly_sets_the_context_in_on_subscribe(server):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const CTX = 'testContext';
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.subscribe({{
            query: `subscription context {{
                context
            }}`,
            variables: {{}},
            context: CTX,
            }}, (error, result) => {{
                client.unsubscribeAll();
                if (error) {{
                  console.log(JSON.stringify(error));
                }}
                if (result) {{
                  console.log(JSON.stringify({{
                    client: {{
                      result: result,
                    }}
                  }}));
                }} else {{
                  // pass
                }}
            }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(.2)
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT), json=['context', {}])
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    client = ret_values['client']
    assert client['result']['context']
    assert client['result']['context'] == 'testContext'


def test_passes_through_websocket_request_to_on_subscribe(server):
    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.subscribe({{
            query: `subscription context {{
                context
            }}`,
            variables: {{}},
            }}, (error, result) => {{
                if (error) {{
                  console.log(JSON.stringify(error));
                }}
            }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    try:
        subprocess.check_output(
            ['node', '-e', node_script], stderr=subprocess.STDOUT, timeout=.2)
    except:
        while True:
            mock = server.get_nowait()
            if mock.name == 'on_subscribe':
                mock.assert_called_once()
                mock.assert_called_with_contains('websocket')
                break


def test_does_not_send_subscription_data_after_client_unsubscribes(server):

    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        setTimeout(function () {{
            let subId = client.subscribe({{
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
                }}
            );
            client.unsubscribe(subId);
        }}, 100);
        client.client.onmessage = (message) => {{
            let msg = JSON.parse(message.data)
            console.log(JSON.stringify({{[msg.type]: msg}}))
        }};
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(.2)
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT), json=['user', {}])
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    with pytest.raises(KeyError):
        assert ret_values[SUBSCRIPTION_DATA]


# TODO: Need to look into why this test is throwing code 1006, not 1002 like
# it should be (1006 more general than 1002 protocol error)
def test_rejects_client_that_does_not_specifiy_a_supported_protocol(server):

    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const client = new WebSocket('ws://localhost:{1}/socket')
        client.on('close', (code) => {{
            console.log(JSON.stringify(code))
          }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = []
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values.append(line)
        except ValueError:
            pass
        except queue.Empty:
            break
    assert ret_values[0] == 1002 or 1006


def test_rejects_unparsable_message(server):

    node_script = '''
        module.paths.push('{0}');
        WebSocket = require('ws');
        const GRAPHQL_SUBSCRIPTIONS = 'graphql-subscriptions';
        const client = new WebSocket('ws://localhost:{1}/socket',
        GRAPHQL_SUBSCRIPTIONS);
        client.onmessage = (message) => {{
            let msg = JSON.parse(message.data)
            console.log(JSON.stringify({{[msg.type]: msg}}))
            client.close();
        }};
        client.onopen = () => {{
            client.send('HI');
        }}
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    assert ret_values['subscription_fail']
    assert len(ret_values['subscription_fail']['payload']['errors']) > 0


def test_rejects_nonsense_message(server):

    node_script = '''
        module.paths.push('{0}');
        WebSocket = require('ws');
        const GRAPHQL_SUBSCRIPTIONS = 'graphql-subscriptions';
        const client = new WebSocket('ws://localhost:{1}/socket',
        GRAPHQL_SUBSCRIPTIONS);
        client.onmessage = (message) => {{
            let msg = JSON.parse(message.data)
            console.log(JSON.stringify({{[msg.type]: msg}}))
            client.close();
        }};
        client.onopen = () => {{
            client.send(JSON.stringify({{}}));
        }}
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    assert ret_values['subscription_fail']
    assert len(ret_values['subscription_fail']['payload']['errors']) > 0


def test_does_not_crash_on_unsub_from_unknown_sub(server):

    node_script = '''
        module.paths.push('{0}');
        WebSocket = require('ws');
        const GRAPHQL_SUBSCRIPTIONS = 'graphql-subscriptions';
        const client = new WebSocket('ws://localhost:{1}/socket',
        GRAPHQL_SUBSCRIPTIONS);
        setTimeout(function () {{
            client.onopen = () => {{
                const SUBSCRIPTION_END = 'subscription_end';
                let subEndMsg = {{type: SUBSCRIPTION_END, id: 'toString'}}
                client.send(JSON.stringify(subEndMsg));
            }}
        }}, 200);
        client.onmessage = (message) => {{
            let msg = JSON.parse(message.data)
            console.log(JSON.stringify({{[msg.type]: msg}}))
        }};
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = []
    while True:
        try:
            line = q.get_nowait()
            ret_values.append(line)
        except ValueError:
            pass
        except queue.Empty:
            break
    assert len(ret_values) == 0


def test_sends_back_any_type_of_error(server):

    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.subscribe({{
            query: `invalid useInfo {{
                error
            }}`,
            variables: {{}},
        }}, function (errors, result) {{
                client.unsubscribeAll();
                if (errors) {{
                    console.log(JSON.stringify({{'errors': errors}}))
                }}
                if (result) {{
                    console.log(JSON.stringify({{'result': result}}))
                }}
            }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(5)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    assert len(ret_values['errors']) > 0


def test_handles_errors_prior_to_graphql_execution(server_with_on_sub_handler):

    node_script = '''
        module.paths.push('{0}')
        WebSocket = require('ws')
        const SubscriptionClient =
        require('subscriptions-transport-ws').SubscriptionClient
        const client = new SubscriptionClient('ws://localhost:{1}/socket')
        client.subscribe({{
            query: `subscription context {{
                context
            }}`,
            variables: {{}},
            context: {{}},
        }}, function (errors, result) {{
                client.unsubscribeAll();
                if (errors) {{
                    console.log(JSON.stringify({{'errors': errors}}))
                }}
                if (result) {{
                    console.log(JSON.stringify({{'result': result}}))
                }}
            }}
        );
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    time.sleep(.2)
    requests.post(
        'http://localhost:{0}/publish'.format(TEST_PORT), json=['context', {}])
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.2)
    ret_values = {}
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            line = json.loads(line)
            ret_values[list(line.keys())[0]] = line[list(line.keys())[0]]
        except ValueError:
            pass
        except queue.Empty:
            break
    assert isinstance(ret_values['errors'], list)
    assert ret_values['errors'][0]['message'] == 'bad'


def test_sends_a_keep_alive_signal_in_the_socket(server_with_keep_alive):

    node_script = '''
        module.paths.push('{0}');
        WebSocket = require('ws');
        const GRAPHQL_SUBSCRIPTIONS = 'graphql-subscriptions';
        const KEEP_ALIVE = 'keepalive';
        const client = new WebSocket('ws://localhost:{1}/socket',
        GRAPHQL_SUBSCRIPTIONS);
        let yieldCount = 0;
        client.onmessage = (message) => {{
            let msg = JSON.parse(message.data)
            if (msg.type === KEEP_ALIVE) {{
                yieldCount += 1;
                if (yieldCount > 1) {{
                let returnMsg = {{'type': msg.type, 'yieldCount': yieldCount}}
                console.log(JSON.stringify(returnMsg))
                client.close();
                }}
            }}
        }};
    '''.format(
        os.path.join(os.path.dirname(__file__), 'node_modules'), TEST_PORT)

    p = subprocess.Popen(
        ['node', '-e', node_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    q = queue.Queue()
    t = threading.Thread(target=enqueue_output, args=(p.stdout, q))
    t.daemon = True
    t.start()
    time.sleep(.5)
    while True:
        try:
            _line = q.get_nowait()
            if isinstance(_line, bytes):
                line = _line.decode()
            ret_value = json.loads(line)
        except ValueError:
            pass
        except queue.Empty:
            break
    assert ret_value['type'] == 'keepalive'
    assert ret_value['yieldCount'] > 1
