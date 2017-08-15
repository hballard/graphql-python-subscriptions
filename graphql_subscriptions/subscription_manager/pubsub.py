from future import standard_library
standard_library.install_aliases()
from builtins import object
import pickle
import sys

from promise import Promise
import redis

from ..executors.gevent import GeventExecutor
from ..executors.asyncio import AsyncioExecutor

PY3 = sys.version_info[0] == 3


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
            except:
                raise ImportError(
                    'You need the redis client "aredis" installed for use w/ '
                    'asyncio')

            redis_client = aredis
        else:
            redis_client = redis

        # patch redis socket library so it doesn't block if using gevent
        if executor == GeventExecutor:
            redis_client.connection.socket = executor.socket

        self.redis = redis_client.StrictRedis(host, port, *args, **kwargs)
        self.pubsub = self.redis.pubsub(ignore_subscribe_messages=True)

        self.executor = executor()
        self.backgrd_task = None

        self.subscriptions = {}
        self.sub_id_counter = 0

    def publish(self, trigger_name, message):
        self.executor.execute(self.redis.publish, trigger_name,
                              pickle.dumps(message))
        return True

    def subscribe(self, trigger_name, on_message_handler, options):
        self.sub_id_counter += 1

        self.subscriptions[self.sub_id_counter] = [
            trigger_name, on_message_handler]

        if PY3:
            trigger_name = trigger_name.encode()

        if trigger_name not in list(self.pubsub.channels.keys()):
            self.executor.join(self.executor.execute(self.pubsub.subscribe,
                                                     trigger_name))
        if not self.backgrd_task:
            self.backgrd_task = self.executor.execute(
                self.wait_and_get_message)

        return Promise.resolve(self.sub_id_counter)

    def unsubscribe(self, sub_id):
        trigger_name, on_message_handler = self.subscriptions[sub_id]
        del self.subscriptions[sub_id]

        if PY3:
            trigger_name = trigger_name.encode()

        if trigger_name not in list(self.pubsub.channels.keys()):
            self.executor.execute(self.pubsub.unsubscribe, trigger_name)

        if not self.subscriptions:
            self.backgrd_task = self.executor.kill(self.backgrd_task)

    async def _wait_and_get_message_async(self):
        try:
            while True:
                message = await self.pubsub.get_message()
                if message:
                    self.handle_message(message)
                await self.executor.sleep(.001)
        except self.executor.task_cancel_error:
            return

    def _wait_and_get_message_sync(self):
        while True:
            message = self.pubsub.get_message()
            if message:
                self.handle_message(message)
            self.executor.sleep(.001)

    def wait_and_get_message(self):
        if hasattr(self.executor, 'loop'):
            return self._wait_and_get_message_async()
        return self._wait_and_get_message_sync()

    def handle_message(self, message):

        channel = message['channel'].decode() if PY3 else message['channel']

        for sub_id, trigger_map in self.subscriptions.items():
            if trigger_map[0] == channel:
                trigger_map[1](pickle.loads(message['data']))
