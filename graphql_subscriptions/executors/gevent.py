from geventwebsocket import WebSocketApplication
import gevent


class GeventMixin(WebSocketApplication):
    pass


class GeventExecutor(object):
    socket = gevent.socket

    @staticmethod
    def sleep(time):
        return gevent.sleep(time)

    @staticmethod
    def kill(coro):
        return gevent.kill(coro)

    @staticmethod
    def join(coro):
        return gevent.joinall([coro])

    @staticmethod
    def execute(fn, *args, **kwargs):
        return gevent.spawn(fn, *args, **kwargs)
