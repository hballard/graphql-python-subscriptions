import json
from geventwebsocket import WebSocketApplication
from promise import Promise

SUBSCRIPTION_FAIL = 'subscription_fail'
SUBSCRIPTION_END = 'subscription_end'
SUBSCRIPTION_DATA = 'subscription_data'
SUBSCRIPTION_START = 'subscription_start'
SUBSCRIPTION_SUCCESS = 'subscription_success'
KEEPALIVE = 'keepalive'
INIT = 'init'
INIT_SUCCESS = 'init_success'
INIT_FAIL = 'init_fail'
GRAPHQL_SUBSCRIPTIONS = 'graphql-subscriptions'

# TODO: Implement 'keep_alive' message sent to client that is in
# apollo subscription-transport constructor


class ApolloSubscriptionServer(WebSocketApplication):

    def __init__(self, subscription_manager, websocket):
        assert subscription_manager, "Must provide\
            'subscription_manager' to websocket app constructor"
        self.subscription_manager = subscription_manager
        self.connection_subscriptions = {}
        self.connection_context = {}
        super(ApolloSubscriptionServer, self).__init__(websocket)

    def unsubscribe(self, graphql_sub_id):
        self.subscription_manager.unsubscribe(graphql_sub_id)

    def on_open(self):
        if self.ws.protocol is None or GRAPHQL_SUBSCRIPTIONS not in self.ws.protocol:
            self.ws.close(1002)

    def on_close(self):
        for sub_id in self.connection_subscriptions.keys():
            self.unsubscribe(self.connection_subscriptions[sub_id])
            del self.connection_subscriptions[sub_id]

    def on_message(self, message):
        class nonlocal:
            on_init_resolve = None
            on_init_reject = None

        def _init_promise_handler(resolve, reject):
            nonlocal.on_init_resolve = resolve
            nonlocal.on_init_reject = reject

        self.connection_context['init_promise'] = Promise(_init_promise_handler)

        try:
            parsed_message = json.loads(message)
        except Exception as e:
            self.send_subscription_fail(
                None,
                {'errors': [{'message': str(e)}]}
            )
        sub_id = parsed_message.get('id')

        if parsed_message.get('type') == INIT:

            on_connect_promise = Promise.resolve(True)
            nonlocal.on_init_resolve(on_connect_promise)

            def _init_success_handler(result):
                if not result:
                    raise TypeError('Prohibited connection!')
                return {'type': INIT_SUCCESS}

            self.connection_context['init_promise'].then(
                _init_success_handler
            ).catch(
                lambda error: {
                    'type': INIT_FAIL,
                    'error': str(error)}
            ).then(
                lambda result: self.send_init_result(result)
            )

        elif parsed_message.get('type') == SUBSCRIPTION_START:

            def _subscription_start_handler(init_result):
                base_params = {
                    'query': parsed_message.get('query'),
                    'variables': parsed_message.get('variables'),
                    'operation_name': parsed_message.get('operation_name'),
                    'context': init_result if isinstance(
                        init_result, dict) else {},
                    'format_response': None,
                    'format_error': None,
                    'callback': None
                }
                promised_params = Promise.resolve(base_params)

                if self.connection_subscriptions[sub_id]:
                    self.unsubscribe(self.connection_subscriptions[sub_id])
                    del self.connection_subscriptions[sub_id]

                def _promised_params_handler(params):
                    if not isinstance(params, dict):
                        error = 'Invalid params returned from\
                                OnSubscribe!  Return value must\
                                be an dict'
                        self.send_subscription_fail(sub_id, {
                            'errors': [{'message': error}]
                        })
                        raise TypeError(error)

                    def _params_callback(error, result):
                        if not error:
                            self.send_subscription_data(sub_id, result)
                        elif error.errors:
                            self.send_subscription_data(sub_id, {
                                'errors': error.errors
                            })
                        elif error.message:
                            self.send_subscription_data(sub_id, {
                                'errors': [{'message': error.message}]
                            })
                        elif error.get('message'):
                            self.send_subscription_data(sub_id, {
                                'errors': [{'message': error.get('message')}]
                            })
                        else:
                            self.send_subscription_data(sub_id, {
                                'errors': [{'message': str(error)}]
                            })

                    params['callback'] = _params_callback

                    return self.subscription_manager.subscribe(**params)

                def _graphql_sub_id_handler(graphql_sub_id):
                    self.connection_subscriptions[sub_id] = graphql_sub_id
                    self.send_subscription_success(sub_id)

                def _error_catch_handler(e):
                    if e.errors:
                        self.send_subscription_fail(sub_id, {
                            'errors': e.errors
                        })
                    elif e.message:
                        self.send_subscription_fail(sub_id, {
                            'errors': [{'message': e.message}]
                        })
                    elif e.get('message'):
                        self.send_subscription_fail(sub_id, {
                            'errors': [{'message': e.get('message')}]
                        })
                    else:
                        self.send_subscription_fail(sub_id, {
                            'errors': [{'message': str(e)}]
                        })

                promised_params.then(
                    _promised_params_handler
                ).then(
                    _graphql_sub_id_handler
                ).catch(
                    _error_catch_handler
                )

            self.connection_context['init_promise'].then(
                _init_promise_handler)

        elif parsed_message.get('type') == SUBSCRIPTION_END:

            def _subscription_end_handler(result):
                if isinstance(self.connection_subscriptions[sub_id], None):
                    self.unsubscribe(self.connection_subscriptions[sub_id])
                    del self.connection_subscriptions[sub_id]

            self.connection_context['init_promise'].then(
                _subscription_end_handler
            )

        else:

            self.send_subscription_fail(sub_id, {
                'errors': [{
                    'message': 'Invalid message type!'
                }]
            })

    def send_subscription_data(self, sub_id, payload):
        message = {
            'type': SUBSCRIPTION_DATA,
            'id': sub_id,
            'payload': payload
        }
        self.ws.send(json.dumps(message))

    def send_subscription_fail(self, sub_id, payload):
        message = {
            'type': SUBSCRIPTION_FAIL,
            'id': sub_id,
            'payload': payload
        }
        self.ws.send(json.dumps(message))

    def send_subscription_success(self, sub_id):
        message = {'type': SUBSCRIPTION_SUCCESS, 'id': sub_id}
        self.ws.send(json.dumps(message))

    def send_init_result(self, result):
        self.ws.send(json.dumps(result))  # may need to use promise here
        if result.get('type') == INIT_FAIL:
            self.ws.close(1011)

    def send_keep_alive(self):
        message = {'type': KEEPALIVE}
        self.ws.send(json.dumps(message))
