import pytest
import redis
import graphene
import gevent
import sys
from promise import Promise
from graphql_subscriptions import (
    RedisPubsub,
    SubscriptionManager,
)


@pytest.fixture
def mock_redis(monkeypatch):
    import fakeredis
    monkeypatch.setattr(redis, 'StrictRedis', fakeredis.FakeStrictRedis)


@pytest.fixture()
def pubsub(mock_redis):
    return RedisPubsub()


@pytest.mark.parametrize('test_input, expected', [
    ('test', 'test'),
    ({1: 'test'}, {1: 'test'}),
    (None, None)
])
def test_pubsub_subscribe_and_publish(pubsub, test_input, expected):

    def message_callback(message):
        try:
            assert message == expected
            pubsub.greenlet.kill()
        except AssertionError as e:
            sys.exit(e)

    def publish_callback(sub_id):
        assert pubsub.publish('a', test_input)
        pubsub.greenlet.join()

    p1 = pubsub.subscribe('a', message_callback, {})
    p2 = p1.then(publish_callback)
    p2.get()


def test_pubsub_subscribe_and_unsubscribe(pubsub):

    def message_callback(message):
        sys.exit('Message callback should not have been called')

    def unsubscribe_publish_callback(sub_id):
        pubsub.unsubscribe(sub_id)
        assert pubsub.publish('a', 'test')
        gevent.sleep(.01)

    p1 = pubsub.subscribe('a', message_callback, {})
    p2 = p1.then(unsubscribe_publish_callback)
    p2.get()


@pytest.fixture
def schema():
    class Query(graphene.ObjectType):
        test_string = graphene.String()

        def resolve_test_string(self, args, context, info):
            return 'works'

    class Subscription(graphene.ObjectType):
        test_subscription = graphene.String()
        test_context = graphene.String()
        test_filter = graphene.String(filterBoolean=graphene.Boolean())
        test_filter_multi = graphene.String(
            filter_boolean=graphene.Boolean(),
            a=graphene.String(),
            b=graphene.Int()
        )
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
def sub_mgr(pubsub, schema):

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
        return {
            'trigger_1': {
                'channel_options': {
                    'foo': 'bar'
                }
            }
        }

    def filter_context(**kwargs):
        return {
            'context_trigger': lambda root, context: context == 'trigger'
        }

    return SubscriptionManager(
        schema,
        pubsub,
        setup_funcs={
            'test_filter': filter_single,
            'test_filter_multi': filter_multi,
            'test_channel_options': filter_channel_options,
            'test_context': filter_context
        }
    )


def test_query_is_valid_and_throws_error(sub_mgr):
    query = 'query a{ testInt }'

    def handler(error):
        assert error.message == 'Subscription query has validation errors'

    p1 = sub_mgr.subscribe(query, 'a', lambda: None, {}, {}, None, None)
    p2 = p1.catch(handler)
    p2.get()


# TODO: Still need to build this validation and add to library
def test_rejects_subscription_with_multiple_root_fields(sub_mgr):
    pass


def test_subscribe_valid_query_return_sub_id(sub_mgr):
    query = 'subscription X{ testSubscription }'

    def handler(sub_id):
        assert isinstance(sub_id, int)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, 'X', lambda: None, {}, {}, None, None)
    p2 = p1.then(handler)
    p2.get()


def test_subscribe_nameless_query_and_return_sub_id(sub_mgr):
    query = 'subscription { testSubscription }'

    def handler(sub_id):
        assert isinstance(sub_id, int)
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(query, None, lambda: None, {}, {}, None, None)
    p2 = p1.then(handler)
    p2.get()


def test_subscribe_with_valid_query_return_root_value(sub_mgr):
    query = 'subscription X{ testSubscription }'

    def callback(e, payload):
        try:
            assert payload.data.get('testSubscription') == 'good'
            sub_mgr.pubsub.greenlet.kill()
        except AssertionError as e:
            sys.exit(e)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('testSubscription', 'good')
        sub_mgr.pubsub.greenlet.join()
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
                    sub_mgr.pubsub.greenlet.kill()
            except AssertionError as e:
                sys.exit(e)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('filter_1', {'filterBoolean': False})
        sub_mgr.publish('filter_1', {'filterBoolean': True})
        sub_mgr.pubsub.greenlet.join()
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(
        query,
        'Filter1',
        callback,
        {'filterBoolean': True},
        {},
        None,
        None
    )
    p2 = p1.then(publish_and_unsubscribe_handler)
    p2.get()


def test_use_filter_func_that_returns_promise(sub_mgr):
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
                    sub_mgr.pubsub.greenlet.kill()
            except AssertionError as e:
                sys.exit(e)

    def publish_and_unsubscribe_handler(sub_id):
        sub_mgr.publish('filter_2', {'filterBoolean': False})
        sub_mgr.publish('filter_2', {'filterBoolean': True})
        sub_mgr.pubsub.greenlet.join()
        sub_mgr.unsubscribe(sub_id)

    p1 = sub_mgr.subscribe(
        query,
        'Filter2',
        callback,
        {'filterBoolean': True},
        {},
        None,
        None
    )
    p2 = p1.then(publish_and_unsubscribe_handler)
    p2.get()
