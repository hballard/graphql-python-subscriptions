import redis
import gevent
from types import FunctionType
from promise import Promise
from graphql import parse, validate, specified_rules, value_from_ast, execute
from graphql.language.ast import OperationDefinition


class RedisPubsub(object):

    def __init__(self, host='localhost', port=6379, *args, **kwargs):
        redis.connection.socket = gevent.socket  # may not need this -- test
        self.redis = redis.StrictRedis(host, port, *args, **kwargs)
        self.pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.subscriptions = {}
        self.sub_id_counter = 0
        self.greenlet = None

    def publish(self, trigger_name, message):
        self.redis.publish(trigger_name, message)
        return True

    def subscribe(self, trigger_name, on_message_handler, options):
        trigger = {}
        trigger[trigger_name] = on_message_handler
        self.pubsub.subscribe(**trigger)
        if not self.greenlet:
            self.greenlet = gevent.spawn(self.wait_and_get_message)
        self.sub_id_counter += 1
        self.subscriptions[self.sub_id_counter] = trigger_name
        return Promise.resolve(self.sub_id_counter)

    def unsubscribe(self, sub_id):
        trigger_name = self.subscriptions[sub_id]
        del self.subscriptions[sub_id]
        self.pubsub.unsubscribe(trigger_name)
        if not self.subscriptions:
            self.greenlet = self.greenlet.kill()

    def wait_and_get_message(self):
        while True:
            self.pubsub.get_message()
            gevent.sleep(.001)  # may not need this sleep call - test


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
        errors = validate(
            self.schema,
            parsed_query,
            # TODO: Need to create/add subscriptionHasSingleRootField
            # rule
            rules=specified_rules
        )

        if errors:
            return Promise.reject(ValidationError(errors))

        args = {}

        subscription_name = ''

        for definition in parsed_query.definitions:

            if isinstance(definition, OperationDefinition):
                root_field = definition.selection_set.selections[0]
                subscription_name = root_field.name.value

                fields = self.schema.get_subscription_type().get_fields()

                for arg in root_field.arguments:

                    arg_definition = [arg_def for arg_def in
                                      fields[subscription_name].args if
                                      arg_def.name == arg.name.value][0]

                    args[arg_definition.name] = value_from_ast(
                        arg.value,
                        arg_definition.type,
                        variables=variables
                    )

        if self.setup_funcs[subscription_name]:
            trigger_map = self.setup_funcs[subscription_name](
                query,
                operation_name,
                callback,
                variables,
                context,
                format_error,
                format_response,
                args,
                subscription_name
            )
        else:
            trigger_map = {}
            trigger_map[subscription_name] = {}

        external_subscription_id = self.max_subscription_id
        self.max_subscription_id += 1
        self.subscriptions[external_subscription_id] = []
        subscription_promises = []

        for trigger_name in trigger_map.keys():
            channel_options = trigger_map[trigger_name].get(
                'channel_options',
                {}
            )
            filter_func = trigger_map[trigger_name].get(
                'filter_func',
                True
            )

            def on_message(root_value):

                def context_promise_handler():
                    if isinstance(context, FunctionType):
                        return context()
                    else:
                        return context

                def filter_func_promise_handler(context):
                    return Promise.all([
                        context,
                        filter_func(root_value, context)
                    ])

                def context_do_execute_handler(result):
                    root_value, do_execute = result
                    if not do_execute:
                        return
                    execute(
                        self.schema,
                        parsed_query,
                        root_value,
                        context,
                        variables,
                        operation_name
                    ).then(
                        lambda data: callback(None, data)
                    )

                return Promise.resolve(
                ).then(
                    context_promise_handler
                ).then(
                    filter_func_promise_handler
                ).then(
                    context_do_execute_handler
                ).catch(
                    lambda error: callback(error)
                )

            subscription_promises.append(
                self.pubsub.subscribe(
                    trigger_name,
                    on_message,
                    channel_options
                ).then(
                    lambda id: self.subscriptions[
                        external_subscription_id].append(id)
                )
            )

        return Promise.all(subscription_promises).then(
            lambda result: external_subscription_id
        )

    def unsubscribe(self, sub_id):
        for internal_id in self.subscriptions.get(sub_id):
            self.pubsub.unsubscribe(internal_id)
        self.subscriptions.pop(sub_id, None)
