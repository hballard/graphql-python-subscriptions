from builtins import str
from promise import Promise
import json

from .message_types import (SUBSCRIPTION_FAIL, SUBSCRIPTION_END,
                            SUBSCRIPTION_DATA, SUBSCRIPTION_START,
                            SUBSCRIPTION_SUCCESS, KEEPALIVE, INIT,
                            INIT_SUCCESS, INIT_FAIL, GRAPHQL_SUBSCRIPTIONS)


class SubscriptionServer(object):
    def __init__(self,
                 subscription_manager,
                 websocket,
                 executor=None,
                 keep_alive=None,
                 on_subscribe=None,
                 on_unsubscribe=None,
                 on_connect=None,
                 on_disconnect=None):

        assert subscription_manager, ("Must provide\
            'subscription_manager' to websocket app constructor")

        self.subscription_manager = subscription_manager
        self.on_subscribe = on_subscribe
        self.on_unsubscribe = on_unsubscribe
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.keep_alive_period = keep_alive
        self.connection_subscriptions = {}
        self.connection_context = {}

        if executor:
            self.executor = executor()
        else:
            self.executor = subscription_manager.pubsub.executor

        self.ws = self.executor.ws = websocket

    def _handle_sync(self):
        self.on_open()

        while True:
            try:
                message = self.executor.ws_recv()
            except self.executor.error:
                self.on_close()
                break

            self.on_message(message)

    async def _handle_async(self):
        self.on_open()

        while True:
            try:
                message = await self.executor.ws_recv()
            except self.executor.error:
                self.on_close()
                break

            self.on_message(message)

    def handle(self):
        if hasattr(self.executor, 'loop'):
            return self._handle_async()
        else:
            return self._handle_sync()

    def unsubscribe(self, graphql_sub_id):
        self.subscription_manager.unsubscribe(graphql_sub_id)

        if self.on_unsubscribe:
            self.on_unsubscribe(self.ws)

    async def _timer_async(self, callback, period):
        try:
            while True:
                callback()
                await self.executor.sleep(period)
        except self.executor.task_cancel_error:
            return

    def _timer_sync(self, callback, period):
        while True:
            callback()
            self.executor.sleep(period)

    def timer(self, callback, period):
        if hasattr(self.executor, 'loop'):
            return self._timer_async(callback, period)
        return self._timer_sync(callback, period)

    def on_open(self):
        if self.executor.ws_protocol() is None or (
                GRAPHQL_SUBSCRIPTIONS not in self.executor.ws_protocol()):
            self.executor.execute(self.executor.ws_close, 1002)

        def keep_alive_callback():
            if self.executor.ws_isopen():
                self.send_keep_alive()
            else:
                self.executor.kill(keep_alive_task)

        if self.keep_alive_period:
            keep_alive_task = self.executor.execute(
                self.timer, keep_alive_callback, self.keep_alive_period)

    def on_close(self):
        for sub_id in list(self.connection_subscriptions.keys()):
            self.unsubscribe(self.connection_subscriptions[sub_id])
            del self.connection_subscriptions[sub_id]

        if self.on_disconnect:
            self.on_disconnect(self.ws)

    def on_message(self, msg):

        if msg is None:
            return
        elif hasattr(msg, 'result'):  # check if future from asyncio
            msg = msg.result()

        non_local = {'on_init_resolve': None, 'on_init_reject': None}

        def init_promise_handler(resolve, reject):
            non_local['on_init_resolve'] = resolve
            non_local['on_init_reject'] = reject

        self.connection_context['init_promise'] = Promise(init_promise_handler)

        def on_message_return_handler(message):
            try:
                parsed_message = json.loads(message)
            except Exception as e:
                self.send_subscription_fail(None,
                                            {'errors': [{
                                                'message': str(e)
                                            }]})
                return

            sub_id = parsed_message.get('id')

            if parsed_message.get('type') == INIT:

                on_connect_promise = Promise.resolve(True)

                if self.on_connect:
                    on_connect_promise = Promise.resolve(
                        self.on_connect(
                            parsed_message.get('payload'), self.ws))

                non_local['on_init_resolve'](on_connect_promise)

                def init_success_promise_handler(result):
                    if not result:
                        raise TypeError('Prohibited connection!')
                    return {'type': INIT_SUCCESS}

                self.connection_context['init_promise'].then(
                    init_success_promise_handler
                ).catch(
                    lambda error: {
                        'type': INIT_FAIL,
                        'error': str(error)}
                ).then(
                    lambda result: self.send_init_result(result)
                )

            elif parsed_message.get('type') == SUBSCRIPTION_START:

                def subscription_start_promise_handler(init_result):
                    base_params = {
                        'query': parsed_message.get('query'),
                        'operation_name': parsed_message.get('operation_name'),
                        'callback': None,
                        'variables': parsed_message.get('variables'),
                        'context':
                        init_result if isinstance(init_result, dict) else
                        parsed_message.get('context', {}),
                        'format_error': None,
                        'format_response': None
                    }
                    promised_params = Promise.resolve(base_params)

                    if self.on_subscribe:
                        promised_params = Promise.resolve(
                            self.on_subscribe(parsed_message, base_params,
                                              self.ws))

                    if self.connection_subscriptions.get(sub_id):
                        self.unsubscribe(self.connection_subscriptions[sub_id])
                        del self.connection_subscriptions[sub_id]

                    def promised_params_handler(params):
                        if not isinstance(params, dict):
                            error = ('Invalid params returned from'
                                     'OnSubscribe!  Return value must be an'
                                     'dict')

                            self.send_subscription_fail(
                                sub_id, {'errors': [{
                                    'message': error
                                }]})
                            raise TypeError(error)

                        def params_callback(error, result):
                            if not error:
                                self.send_subscription_data(
                                    sub_id, {'data': result.data})
                            elif hasattr(error, 'message'):
                                self.send_subscription_data(
                                    sub_id,
                                    {'errors': [{
                                        'message': error.message
                                    }]})
                            elif hasattr(error, 'errors'):
                                self.send_subscription_data(
                                    sub_id, {'errors': error.errors})
                            else:
                                self.send_subscription_data(
                                    sub_id,
                                    {'errors': [{
                                        'message': str(error)
                                    }]})

                        params['callback'] = params_callback

                        return self.subscription_manager.subscribe(**params)

                    def graphql_sub_id_promise_handler(graphql_sub_id):
                        self.connection_subscriptions[sub_id] = graphql_sub_id
                        self.send_subscription_success(sub_id)

                    def error_catch_handler(e):
                        if hasattr(e, 'errors'):
                            self.send_subscription_fail(
                                sub_id, {'errors': e.errors})
                        elif hasattr(e, 'message'):
                            self.send_subscription_fail(
                                sub_id, {'errors': [{
                                    'message': e.message
                                }]})
                        elif e.get('message'):
                            self.send_subscription_fail(
                                sub_id,
                                {'errors': [{
                                    'message': e.get('message')
                                }]})
                        else:
                            self.send_subscription_fail(
                                sub_id, {'errors': [{
                                    'message': str(e)
                                }]})

                    promised_params.then(promised_params_handler).then(
                        graphql_sub_id_promise_handler).catch(
                            error_catch_handler)

                # Promise from init statement (line 54)
                # seems to reset between if statements
                # not sure if this behavior is correct or
                # not per promises A spec...need to
                # investigate
                non_local['on_init_resolve'](Promise.resolve(True))

                self.connection_context['init_promise'].then(
                    subscription_start_promise_handler)

            elif parsed_message.get('type') == SUBSCRIPTION_END:

                def subscription_end_promise_handler(result):
                    if self.connection_subscriptions.get(sub_id):
                        self.unsubscribe(self.connection_subscriptions[sub_id])
                        del self.connection_subscriptions[sub_id]

                # same rationale as above
                non_local['on_init_resolve'](Promise.resolve(True))

                self.connection_context['init_promise'].then(
                    subscription_end_promise_handler)

            else:

                self.send_subscription_fail(
                    sub_id, {'errors': [{
                        'message': 'Invalid message type!'
                    }]})

        return on_message_return_handler(msg)

    def send_subscription_data(self, sub_id, payload):
        message = {'type': SUBSCRIPTION_DATA, 'id': sub_id, 'payload': payload}
        self.executor.execute(self.executor.ws_send, json.dumps(message))

    def send_subscription_fail(self, sub_id, payload):
        message = {'type': SUBSCRIPTION_FAIL, 'id': sub_id, 'payload': payload}
        self.executor.execute(self.executor.ws_send, json.dumps(message))

    def send_subscription_success(self, sub_id):
        message = {'type': SUBSCRIPTION_SUCCESS, 'id': sub_id}
        self.executor.execute(self.executor.ws_send, json.dumps(message))

    def send_init_result(self, result):
        self.executor.execute(self.executor.ws_send, json.dumps(result))
        if result.get('type') == INIT_FAIL:
            self.executor.execute(self.executor.ws_close, 1011)

    def send_keep_alive(self):
        message = {'type': KEEPALIVE}
        self.executor.execute(self.executor.ws_send, json.dumps(message))
