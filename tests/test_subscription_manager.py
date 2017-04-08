import pytest
import redis
import graphene
from graphql_subscriptions import RedisPubsub, SubscriptionManager


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

    def message_handler(message):
        assert message == expected

    def publish_callback(sub_id):
        assert pubsub.publish('a', test_input)

    pubsub.subscribe('a', message_handler, {}).then(publish_callback)

    while True:
        message = pubsub.pubsub.get_message(ignore_subscribe_messages=True)
        if message:
            pubsub.handle_message(message)
            break


@pytest.mark.parametrize('test_input, expected', [
    ('test', 'test'),
    ({1: 'test'}, {1: 'test'}),
    (None, None)
])
def test_pubsub_subscribe_and_unsubscribe(pubsub, test_input, expected):

    def message_handler(message):
        assert 0

    def unsubscribe_publish_callback(sub_id):
        pubsub.unsubscribe(sub_id)
        assert pubsub.publish('a', test_input)

    pubsub.subscribe('a', message_handler,
                     {}).then(unsubscribe_publish_callback)


@pytest.fixture
def schema():
    class Query(graphene.ObjectType):
        test_string = graphene.String()

        def resolve_test_string(self, args, context, info):
            return 'works'

    class Subscription(graphene.ObjectType):
        test_subscription = graphene.String()
        test_context = graphene.String()
        test_filter = graphene.String(filter_boolean=graphene.Boolean())
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
            return 'good_filter' if args.get('filter_boolean') else 'bad_filter'

        def resolve_test_filter_multi(self, args, context, info):
            return 'good_filter' if args.get('filter_boolean') else 'bad_filter'

        def resolve_test_channel_options(self, args, context, info):
            return self

    return graphene.Schema(query=Query, subscription=Subscription)


@pytest.fixture
def sub_mgr(pubsub, schema):

    def filter_single(**kwargs):
        args = kwargs.get('args')
        return {
            'filter_1': {
                'filter': lambda root, context: root.get('filter_boolean') == args.get('filter_boolean')
            }
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
