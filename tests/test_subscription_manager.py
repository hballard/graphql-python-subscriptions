import pytest
import redis
from graphql_subscriptions import RedisPubsub


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    import fakeredis
    monkeypatch.setattr(redis, 'StrictRedis', fakeredis.FakeStrictRedis)


@pytest.mark.parametrize('test_input, expected', [
    ('test', 'test'),
    ({1: 'test'}, {1: 'test'}),
    (None, None)
])
def test_pubsub_subscribe_and_publish(test_input, expected):
    ps = RedisPubsub()

    def message_handler(message):
        assert message == expected

    def publish_callback(sub_id):
        assert ps.publish('a', test_input)

    ps.subscribe('a', message_handler, {}).then(publish_callback)

    while True:
        message = ps.pubsub.get_message(ignore_subscribe_messages=True)
        if message:
            ps.handle_message(message)
            break


@pytest.mark.parametrize('test_input, expected', [
    ('test', 'test'),
    ({1: 'test'}, {1: 'test'}),
    (None, None)
])
def test_pubsub_subscribe_and_unsubscribe(test_input, expected):
    ps = RedisPubsub()

    def message_handler(message):
        assert 0

    def unsubscribe_publish_callback(sub_id):
        ps.unsubscribe(sub_id)
        assert ps.publish('a', test_input)

    ps.subscribe('a', message_handler, {}).then(unsubscribe_publish_callback)
