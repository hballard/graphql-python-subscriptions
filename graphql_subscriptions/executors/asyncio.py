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

    @staticmethod
    def ws_close(ws, code):
        return ws.close(code)

    @staticmethod
    def ws_protocol(ws):
        return ws.subprotocol

    @staticmethod
    def ws_isopen(ws):
        if ws.open:
            return True
        else:
            return False

    @staticmethod
    def ws_send(ws, msg):
        return ws.send(msg)

    @staticmethod
    def ws_recv(ws):
        return ws.recv()

    @staticmethod
    def sleep(time):
        return asyncio.sleep(time)

    @staticmethod
    @asyncio.coroutine
    def set_timeout(callback, period):
        while True:
            callback()
            yield from asyncio.sleep(period)

    @staticmethod
    def kill(future):
        future.cancel()

    @staticmethod
    def join(future, timeout):
        return asyncio.wait_for(future, timeout=timeout)

    def join_all(self):
        while self.futures:
            futures = self.futures
            self.futures = []
            asyncio.wait(futures)
        return futures

    def execute(self, fn, *args, **kwargs):
        coro = fn(*args, **kwargs)
        future = ensure_future(coro, loop=self.loop)
        self.futures.append(future)
        return future

    def execute_and_call_callback(self, fn, callback, *args, **kwargs):
        coro = fn(*args, **kwargs)
        future = ensure_future(coro, loop=self.loop)
        self.futures.append(future)
        future.add_done_callback(callback)
        return future
