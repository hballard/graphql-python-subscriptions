# source: https://github.com/graphql-python/graphql-core/blob/master/graphql/execution/executors/asyncio.py
from __future__ import absolute_import

import asyncio

try:
    from asyncio import ensure_future
except ImportError:
    # ensure_future is only implemented in Python 3.4.4+
    def ensure_future(coro_or_future, loop=None):
        """Wrap a coroutine or an awaitable in a future.
        If the argument is a Future, it is returned directly.
        """
        if isinstance(coro_or_future, asyncio.Future):
            if loop is not None and loop is not coro_or_future._loop:
                raise ValueError('loop argument must agree with Future')
            return coro_or_future
        elif asyncio.iscoroutine(coro_or_future):
            if loop is None:
                loop = asyncio.get_event_loop()
            task = loop.create_task(coro_or_future)
            if task._source_traceback:
                del task._source_traceback[-1]
            return task
        else:
            raise TypeError('A Future, a coroutine or an awaitable is\
                            required')


class AsyncioMixin(object):
    pass


class AsyncioExecutor(object):

    def __init__(self, loop=None):
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.futures = []

    @staticmethod
    @asyncio.coroutine
    def sleep(time):
        yield from asyncio.sleep(time)

    @staticmethod
    def kill(future):
        future.cancel()

    @staticmethod
    def join(future):
        self.loop.run_until_complete(asyncio.wait_for(future))

    def join_all(self):
        futures = self.futures
        self.futures = []
        self.loop.run_until_complete(asyncio.wait(futures))
        return futures

    def execute(self, fn, *args, **kwargs):
        future = ensure_future(result, loop=self.loop)
        self.futures.append(future)
        return future
