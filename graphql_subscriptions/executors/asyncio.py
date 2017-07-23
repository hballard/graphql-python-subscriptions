from __future__ import absolute_import

import asyncio
from websockets import ConnectionClosed

try:
    from asyncio import ensure_future
except ImportError:
    # ensure_future is only implemented in Python 3.4.4+
    # Reference: https://github.com/graphql-python/graphql-core/blob/master/graphql/execution/executors/asyncio.py
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
            raise TypeError(
                'A Future, a coroutine or an awaitable is required')


class AsyncioExecutor(object):
    error = ConnectionClosed

    def __init__(self, loop=None):
        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self.futures = []

    def ws_close(self, code):
        return self.ws.close(code)

    def ws_protocol(self):
        return self.ws.subprotocol

    def ws_isopen(self):
        if self.ws.open:
            return True
        else:
            return False

    def ws_send(self, msg):
        return self.ws.send(msg)

    def ws_recv(self):
        return self.ws.recv()

    def sleep(self, time):
        if self.loop.is_running():
            return asyncio.sleep(time)
        return self.loop.run_until_complete(asyncio.sleep(time))

    @staticmethod
    def kill(future):
        future.cancel()

    def join(self, future=None, timeout=None):
        if not isinstance(future, asyncio.Future):
            return
        if self.loop.is_running():
            return asyncio.wait_for(future, timeout=timeout)
        return self.loop.run_until_complete(asyncio.wait_for(future,
                                                             timeout=timeout))

    def execute(self, fn, *args, **kwargs):
        result = fn(*args, **kwargs)
        if isinstance(result, asyncio.Future) or asyncio.iscoroutine(result):
            future = ensure_future(result, loop=self.loop)
            self.futures.append(future)
            return future
        return result
