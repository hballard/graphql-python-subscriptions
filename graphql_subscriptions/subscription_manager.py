from future import standard_library
standard_library.install_aliases()
from builtins import object
from types import FunctionType
import pickle

from graphql import parse, validate, specified_rules, value_from_ast, execute
from graphql.language.ast import OperationDefinition
from promise import Promise
import gevent
import redis

from .utils import to_snake_case
from .validation import SubscriptionHasSingleRootField


class RedisPubsub(object):
    def __init__(self, host='localhost', port=6379, *args, **kwargs):
        redis.connection.socket = gevent.socket
        self.redis = redis.StrictRedis(host, port, *args, **kwargs)
        self.pubsub = self.redis.pubsub()
        self.subscriptions = {}
        self.sub_id_counter = 0
        self.greenlet = None

    def publish(self, trigger_name, message):
        self.redis.publish(trigger_name, pickle.dumps(message))
        return True

    def subscribe(self, trigger_name, on_message_handler, options):
        self.sub_id_counter += 1
        try:
            if trigger_name not in list(self.subscriptions.values())[0]:
                self.pubsub.subscribe(trigger_name)
        except IndexError:
            self.pubsub.subscribe(trigger_name)
        self.subscriptions[self.sub_id_counter] = [
            trigger_name, on_message_handler
        ]
        if not self.greenlet:
            self.greenlet = gevent.spawn(self.wait_and_get_message)
        return Promise.resolve(self.sub_id_counter)

    def unsubscribe(self, sub_id):
        trigger_name, on_message_handler = self.subscriptions[sub_id]
        del self.subscriptions[sub_id]
        try:
            if trigger_name not in list(self.subscriptions.values())[0]:
                self.pubsub.unsubscribe(trigger_name)
        except IndexError:
            self.pubsub.unsubscribe(trigger_name)
        if not self.subscriptions:
            self.greenlet = self.greenlet.kill()

    def wait_and_get_message(self):
        while True:
            message = self.pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                self.handle_message(message)
            gevent.sleep(.001)

    def handle_message(self, message):
        if isinstance(message['channel'], bytes):
            channel = message['channel'].decode()
        for sub_id, trigger_map in self.subscriptions.items():
            if trigger_map[0] == channel:
                trigger_map[1](pickle.loads(message['data']))


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        self.message = 'Subscription query has validation errors'


class SubscriptionManager(object):
    def __init__(self, schema, pubsub, setup_funcs={}):
        self.schema = schema
        self.pubsub = pubsub
        self.setup_funcs = setup_funcs
        self.subscriptions = {}
        self.max_subscription_id = 0

    def publish(self, trigger_name, payload):
        self.pubsub.publish(trigger_name, payload)

    def subscribe(self, query, operation_name, callback, variables, context,
                  format_error, format_response):
        parsed_query = parse(query)
        rules = specified_rules + [SubscriptionHasSingleRootField]
        errors = validate(self.schema, parsed_query, rules=rules)

        if errors:
            return Promise.rejected(ValidationError(errors))

        args = {}

        subscription_name = ''

        for definition in parsed_query.definitions:

            if isinstance(definition, OperationDefinition):
                root_field = definition.selection_set.selections[0]
                subscription_name = root_field.name.value

                fields = self.schema.get_subscription_type().fields

                for arg in root_field.arguments:

                    arg_definition = [
                        arg_def
                        for _, arg_def in fields.get(subscription_name)
                        .args.items() if arg_def.out_name == arg.name.value
                    ][0]

                    args[arg_definition.out_name] = value_from_ast(
                        arg.value, arg_definition.type, variables=variables)

        if self.setup_funcs.get(to_snake_case(subscription_name)):
            trigger_map = self.setup_funcs[to_snake_case(subscription_name)](
                query=query,
                operation_name=operation_name,
                callback=callback,
                variables=variables,
                context=context,
                format_error=format_error,
                format_response=format_response,
                args=args,
                subscription_name=subscription_name)
        else:
            trigger_map = {}
            trigger_map[subscription_name] = {}

        external_subscription_id = self.max_subscription_id
        self.max_subscription_id += 1
        self.subscriptions[external_subscription_id] = []
        subscription_promises = []

        for trigger_name in trigger_map.keys():
            try:
                channel_options = trigger_map[trigger_name].get(
                    'channel_options', {})
                filter = trigger_map[trigger_name].get('filter',
                                                       lambda arg1, arg2: True)
            except AttributeError:
                channel_options = {}

                # TODO: Think about this some more...the Apollo library
                # let's all messages through by default, even if
                # the users incorrectly uses the setup_funcs (does not
                # use 'filter' or 'channel_options' keys); I think it
                # would be better to raise an exception here
                def filter(arg1, arg2):
                    return True

            def on_message(root_value):
                def context_promise_handler(result):
                    if isinstance(context, FunctionType):
                        return context()
                    else:
                        return context

                def filter_func_promise_handler(context):
                    return Promise.all([context, filter(root_value, context)])

                def context_do_execute_handler(result):
                    context, do_execute = result
                    if not do_execute:
                        return
                    else:
                        return execute(self.schema, parsed_query, root_value,
                                       context, variables, operation_name)

                return Promise.resolve(True).then(
                    context_promise_handler).then(
                        filter_func_promise_handler).then(
                            context_do_execute_handler).then(
                                lambda result: callback(None, result)).catch(
                                    lambda error: callback(error, None))

            subscription_promises.append(
                self.pubsub.
                subscribe(trigger_name, on_message, channel_options).then(
                    lambda id: self.subscriptions[external_subscription_id].append(id)
                ))

        return Promise.all(subscription_promises).then(
            lambda result: external_subscription_id)

    def unsubscribe(self, sub_id):
        for internal_id in self.subscriptions.get(sub_id):
            self.pubsub.unsubscribe(internal_id)
        self.subscriptions.pop(sub_id, None)
