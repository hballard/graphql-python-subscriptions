from types import FunctionType
import sys

from graphql import validate, parse
from promise import Promise
import fakeredis
import graphene
import pytest
import redis

from graphql_subscriptions import RedisPubsub, SubscriptionManager
from graphql_subscriptions.subscription_manager.validation import (
     SubscriptionHasSingleRootField)
from graphql_subscriptions.executors.gevent import GeventExecutor
# from graphql_subscriptions.executors.asyncio import AsyncioExecutor


@pytest.fixture(params=[GeventExecutor])
def pubsub(monkeypatch, request):
    # monkeypatch.setattr(redis, 'StrictRedis', fakeredis.FakeStrictRedis)
    return RedisPubsub(executor=request.param)


@pytest.fixture
def schema():
    class Query(graphene.ObjectType):
        test_string = graphene.String()

        def resolve_test_string(self, args, context, info):
            return 'works'

    # TODO: Implement case conversion for arg names
    class Subscription(graphene.ObjectType):
        test_subscription = graphene.String()
        test_context = graphene.String()
        test_filter = graphene.String(filterBoolean=graphene.Boolean())
        test_filter_multi = graphene.String(
            filterBoolean=graphene.Boolean(),
            a=graphene.String(),
            b=graphene.Int())
        test_channel_options = graphene.String()

        def resolve_test_subscription(self, args, context, info):
            return self

        def resolve_test_context(self, args, context, info):
            return context

        def resolve_test_filter(self, args, context, info):
            return 'good_filter' if args.get('filterBoolean') else 'bad_filter'

        def resolve_test_filter_multi(self, args, context, info):
            return 'good_filter' if args.get('filterBoolean') else 'bad_filter'

        def resolve_test_channel_options(self, args, context, info):
            return self

    return graphene.Schema(query=Query, subscription=Subscription)


@pytest.fixture
def setup_funcs():
    def filter_single(**kwargs):
        args = kwargs.get('args')
        return {
            'filter_1': {
                'filter': lambda root, context: root.get('filterBoolean') == args.get('filterBoolean')
            },
            'filter_2': {
                'filter': lambda root, context: Promise.resolve(root.get('filterBoolean') == args.get('filterBoolean'))
            },
        }

    def filter_multi(**kwargs):
        return {
            'trigger_1': {
                'filter': lambda root, context: True
            },
            'trigger_2': {
                'filter': lambda root, context: True
            }
        }

    def filter_channel_options(**kwargs):
        return {'trigger_1': {'channel_options': {'foo': 'bar'}}}

    def filter_context(**kwargs):
        return {'context_trigger': lambda root, context: context == 'trigger'}

    return {
        'test_filter': filter_single,
        'test_filter_multi': filter_multi,
        'test_channel_options': filter_channel_options,
        'test_context': filter_context
    }


@pytest.fixture
def sub_mgr(pubsub, schema, setup_funcs):
    return SubscriptionManager(schema, pubsub, setup_funcs)


@pytest.mark.parametrize('test_input, expected', [('test', 'test'), ({
    1: 'test'}, {1: 'test'}), (None, None)])
def test_pubsub_subscribe_and_publish(pubsub, test_input, expected):
    def message_callback(message):
        try:
            assert message == expected
            pubsub.executor.kill(pubsub.backgrd_task)
        except AssertionError as e:
            sys.exit(e)

    def publish_callback(sub_id):
        assert pubsub.publish('a', test_input)
        pubsub.executor.join(pubsub.backgrd_task)

    p1 = pubsub.subscribe('a', message_callback, {})
    p2 = p1.then(publish_callback)
    p2.get()


def test_pubsub_subscribe_and_unsubscribe(pubsub):
    def message_callback(message):
        sys.exit('Message callback should not have been called')

    def unsubscribe_publish_callback(sub_id):
        pubsub.unsubscribe(sub_id)
        assert pubsub.publish('a', 'test')

    p1 = pubsub.subscribe('a', message_callback, {})
    p2 = p1.then(unsubscribe_publish_callback)
    p2.get()


def test_query_is_valid_and_throws_error(sub_mgr):
    query = 'query a{ testInt }'

    def handler(error):
        assert error.message == 'Subscription query has validation errors'

    p1 = sub_mgr.subscribe(query, 'a', lambda: None, {}, {}, None, None)
    p2 = p1.catch(handler)
    p2.get()


def test_rejects_subscription_with_multiple_root_fields(sub_mgr):
    query = 'subscription X{ a: testSubscription, b: testSubscription }'

    def handler(error):
        assert error.message == 'Subscription query has validation errors'

    p1 = sub_mgr.subscribe(query, 'X', lambda: None, {}, {}, None, None)
    p2 = p1.catch(handler)
    p2.get()


def test_subscribe_valid_query_and_return_sub_id(sub_mgr):
    query = 'subscription X{ testSubscription }'

    def handler(sub_id):
        assert isinstance(sub_id, int)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, 'X', lambda: None, {}, {}, None, None)
    p2 = p1.then(handler)
    p2.get()


def test_subscribe_to_nameless_query_and_return_sub_id(sub_mgr):
    query = 'subscription { testSubscription }'

    def handler(sub_id):
        assert isinstance(sub_id, int)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, None, lambda: None, {}, {}, None, None)
    p2 = p1.then(handler)
    p2.get()


def test_subscribe_with_valid_query_and_return_root_value(sub_mgr):
    query = 'subscription X{ testSubscription }'

    def callback(e, payload):
        try:
            assert payload.data.get('testSubscription') == 'good'
            sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)
        except AssertionError as e:
            sys.exit(e)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('testSubscription', 'good')
        sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, 'X', callback, {}, {}, None, None)
    p2 = p1.then(publish_and_unsubscribe_handler)
    p2.get()


def test_use_filter_functions_properly(sub_mgr):
    query = 'subscription Filter1($filterBoolean: Boolean) {\
    testFilter(filterBoolean: $filterBoolean)}'

    def callback(err, payload):
        if err:
            sys.exit(err)
        else:
            try:
                if payload is None:
                    assert True
                else:
                    assert payload.data.get('testFilter') == 'good_filter'
                    sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)
            except AssertionError as e:
                sys.exit(e)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('filter_1', {'filterBoolean': False})
        sub_mgr.publish('filter_1', {'filterBoolean': True})
        sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, 'Filter1', callback, {'filterBoolean': True},
                           {}, None, None)
    p2 = p1.then(publish_and_unsubscribe_handler)
    p2.get()


def test_use_filter_func_that_returns_a_promise(sub_mgr):
    query = 'subscription Filter2($filterBoolean: Boolean) {\
    testFilter(filterBoolean: $filterBoolean)}'

    def callback(err, payload):
        if err:
            sys.exit(err)
        else:
            try:
                if payload is None:
                    assert True
                else:
                    assert payload.data.get('testFilter') == 'good_filter'
                    sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)
            except AssertionError as e:
                sys.exit(e)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('filter_2', {'filterBoolean': False})
        sub_mgr.publish('filter_2', {'filterBoolean': True})
        try:
            sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        except:
            raise
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, 'Filter2', callback, {'filterBoolean': True},
                           {}, None, None)
    p2 = p1.then(publish_and_unsubscribe_handler)
    p2.get()


def test_can_subscribe_to_more_than_one_trigger(sub_mgr):
    non_local = {'trigger_count': 0}

    query = 'subscription multiTrigger($filterBoolean: Boolean,\
            $uga: String){testFilterMulti(filterBoolean: $filterBoolean,\
            a: $uga, b: 66)}'

    def callback(err, payload):
        if err:
            sys.exit(err)
        else:
            try:
                if payload is None:
                    assert True
                else:
                    assert payload.data.get('testFilterMulti') == 'good_filter'
                    non_local['trigger_count'] += 1
            except AssertionError as e:
                sys.exit(e)
        if non_local['trigger_count'] == 2:
            sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('not_a_trigger', {'filterBoolean': False})
        sub_mgr.publish('trigger_1', {'filterBoolean': True})
        sub_mgr.publish('trigger_2', {'filterBoolean': True})
        sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, 'multiTrigger', callback,
                           {'filterBoolean': True,
                            'uga': 'UGA'}, {}, None, None)
    p2 = p1.then(publish_and_unsubscribe_handler)
    p2.get()


def test_subscribe_to_trigger_and_use_pubsub_channel_options(
        sub_mgr, pubsub, mocker):
    query = 'subscription X{ testChannelOptions }'
    spy = mocker.spy(pubsub, 'subscribe')

    def subscribe_handler(sub_id):
        assert spy.call_count == 1

        expected_channel_options = {'foo': 'bar'}

        args, _ = spy.call_args
        trigger, callback, options = args
        assert isinstance(trigger, str)
        assert isinstance(callback, FunctionType)
        assert options == expected_channel_options

    p1 = sub_mgr.subscribe(
        query,
        operation_name='X',
        callback=lambda: None,
        variables={},
        context={},
        format_error=None,
        format_response=None)
    p2 = p1.then(subscribe_handler)
    p2.get()


def test_can_unsubscribe(sub_mgr):
    query = 'subscription X{ testSubscription }'

    def callback(err, payload):
        try:
            assert False
        except Exception as e:
            sys.exit(e)

    def unsubscribe_and_publish_handler(sub_id):
        sub_mgr.unsubscribe(sub_id)
        sub_mgr.publish('testSubscription', 'good')
        try:
            sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        except AttributeError:
            return

    p1 = sub_mgr.subscribe(
        query,
        operation_name='X',
        callback=callback,
        variables={},
        context={},
        format_error=None,
        format_response=None)
    p2 = p1.then(unsubscribe_and_publish_handler)
    p2.get()


def test_throws_exception_when_unsubscribes_from_unknown_id(sub_mgr):
    with pytest.raises(TypeError):
        raise sub_mgr.unsubscribe(123)


def test_throws_error_when_unsubscribes_a_second_time(sub_mgr):
    query = 'subscription X{ testSubscription }'

    def unsubscribe_and_unsubscribe_handler(sub_id):
        sub_mgr.unsubscribe(sub_id)
        with pytest.raises(TypeError):
            sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(
        query,
        operation_name='X',
        callback=lambda: None,
        variables={},
        context={},
        format_error=None,
        format_response=None)
    p2 = p1.then(unsubscribe_and_unsubscribe_handler)
    p2.get()


def test_calls_the_error_callback_if_there_is_an_execution_error(
        sub_mgr):
    query = 'subscription X($uga: Boolean!){\
        testSubscription  @skip(if: $uga)\
    }'

    def callback(err, payload):
        try:
            assert payload is None
            assert err.message == ('Variable "$uga" of required type '
                                   '"Boolean!" was not provided.')

            sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)
        except AssertionError as e:
            sys.exit(e)

    def unsubscribe_and_publish_handler(sub_id):
        sub_mgr.publish('testSubscription', 'good')
        try:
            sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        except AttributeError:
            return
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(
        query,
        operation_name='X',
        callback=callback,
        variables={},
        context={},
        format_error=None,
        format_response=None)
    p2 = p1.then(unsubscribe_and_publish_handler)
    p2.get()


def test_calls_context_if_it_is_a_function(sub_mgr):
    query = 'subscription TestContext { testContext }'

    def callback(err, payload):
        try:
            assert err is None
            assert payload.data.get('testContext') == 'trigger'
            sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)
        except AssertionError as e:
            sys.exit(e)

    def unsubscribe_and_publish_handler(sub_id):
        sub_mgr.publish('context_trigger', 'ignored')
        try:
            sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        except AttributeError:
            return
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(
        query,
        operation_name='TestContext',
        callback=callback,
        variables={},
        context=lambda: 'trigger',
        format_error=None,
        format_response=None)
    p2 = p1.then(unsubscribe_and_publish_handler)
    p2.get()


def test_calls_the_error_callback_if_context_func_throws_error(
        sub_mgr):
    query = 'subscription TestContext { testContext }'

    def callback(err, payload):
        try:
            assert payload is None
            assert str(err) == 'context error'
            sub_mgr.pubsub.executor.kill(sub_mgr.pubsub.backgrd_task)
        except AssertionError as e:
            sys.exit(e)

    def unsubscribe_and_publish_handler(sub_id):
        sub_mgr.publish('context_trigger', 'ignored')
        try:
            sub_mgr.pubsub.executor.join(sub_mgr.pubsub.backgrd_task)
        except AttributeError:
            return
        sub_mgr.unsubscribe(sub_id)

    def context():
        raise Exception('context error')

    p1 = sub_mgr.subscribe(
        query,
        operation_name='TestContext',
        callback=callback,
        variables={},
        context=context,
        format_error=None,
        format_response=None)
    p2 = p1.then(unsubscribe_and_publish_handler)
    p2.get()


# --------------------------------------------------
# validation tests ....


@pytest.fixture
def validation_schema():
    class Query(graphene.ObjectType):
        placeholder = graphene.String()

    class Subscription(graphene.ObjectType):
        test_1 = graphene.String()
        test_2 = graphene.String()

    return graphene.Schema(query=Query, subscription=Subscription)


def test_should_allow_a_valid_subscription(validation_schema):
    sub = 'subscription S1{ test1 }'
    errors = validate(validation_schema,
                      parse(sub), [SubscriptionHasSingleRootField])
    assert len(errors) == 0


def test_should_allow_another_valid_subscription(validation_schema):
    sub = 'subscription S1{ test1 } subscription S2{ test2 }'
    errors = validate(validation_schema,
                      parse(sub), [SubscriptionHasSingleRootField])
    assert len(errors) == 0


def test_should_not_allow_two_fields_in_the_subscription(validation_schema):
    sub = 'subscription S3{ test1 test2 }'
    errors = validate(validation_schema,
                      parse(sub), [SubscriptionHasSingleRootField])
    assert len(errors) == 1
    assert errors[0].message == 'Subscription "S3" must have only one field.'


def test_should_not_allow_inline_fragments(validation_schema):
    sub = 'subscription S4{ ...on Subscription { test1 } }'
    errors = validate(validation_schema,
                      parse(sub), [SubscriptionHasSingleRootField])
    assert len(errors) == 1
    assert errors[0].message == ('Apollo subscriptions do not support '
                                 'fragments on the root field')


def test_should_not_allow_fragments(validation_schema):
    sub = ('subscription S5{ ...testFragment }'
           'fragment testFragment on Subscription{ test2 }')

    errors = validate(validation_schema,
                      parse(sub), [SubscriptionHasSingleRootField])
    assert len(errors) == 1
    assert errors[0].message == ('Apollo subscriptions do not support '
                                 'fragments on the root field')
