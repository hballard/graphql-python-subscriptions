from future import standard_library
standard_library.install_aliases()
from builtins import object
import pickle

from promise import Promise
import redis

from ..executors.gevent import GeventExecutor
from ..executors.asyncio import AsyncioExecutor


class RedisPubsub(object):
    def __init__(self,
                 host='localhost',
                 port=6379,
                 executor=GeventExecutor,
                 *args,
                 **kwargs):

        if executor == AsyncioExecutor:
            try:
                import aredis
            except ImportError:
                print('You need the redis_client "aredis" for use w/ asyncio')

            redis_client = aredis
        else:
            redis_client = redis

        # patch socket library so it doesn't block if using gevent
        if executor == GeventExecutor:
            redis_client.connection.socket = executor.socket

        self.redis = redis_client.StrictRedis(host, port, *args, **kwargs)
        self.pubsub = self.redis.pubsub()

        self.executor = executor()
        self.get_message_task = None

        self.subscriptions = {}
        self.sub_id_counter = 1

    def publish(self, trigger_name, message):
        self.executor.execute(self.redis.publish, trigger_name,
                              pickle.dumps(message))
        return True

    def subscribe(self, trigger_name, on_message_handler, options):
        self.sub_id_counter += 1

        try:
            if trigger_name not in list(self.subscriptions.values())[0]:
                self.executor.execute(self.pubsub.subscribe, trigger_name)
        except IndexError:
            self.executor.execute(self.pubsub.subscribe, trigger_name)
        self.subscriptions[self.sub_id_counter] = [
            trigger_name, on_message_handler
        ]
        if not self.get_message_task:
            self.get_message_task = self.executor.execute(
                self.wait_and_get_message)
        return Promise.resolve(self.sub_id_counter)

    def unsubscribe(self, sub_id):
        trigger_name, on_message_handler = self.subscriptions[sub_id]
        del self.subscriptions[sub_id]

        try:
            if trigger_name not in list(self.subscriptions.values())[0]:
                self.executor.execute(self.pubsub.unsubscribe, trigger_name)
        except IndexError:
            self.executor.execute(self.pubsub.unsubscribe, trigger_name)

        if not self.subscriptions:
            self.get_message_task = self.executor.kill(self.get_message_task)

    def wait_and_get_message(self):
        while True:
            message = self.pubsub.get_message(ignore_subscribe_messages=True)
            if message:
                self.handle_message(message)
            self.executor.sleep(.001)

    def handle_message(self, message):
        if isinstance(message['channel'], bytes):
            channel = message['channel'].decode()

        for sub_id, trigger_map in self.subscriptions.items():
            if trigger_map[0] == channel:
                trigger_map[1](pickle.loads(message['data']))
