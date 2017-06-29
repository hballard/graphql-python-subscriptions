from __future__ import absolute_import

from geventwebsocket import WebSocketApplication
import gevent


class GeventMixin(WebSocketApplication):
    pass


class GeventExecutor(object):
    # used to patch socket library to it doesn't block
    socket = gevent.socket

    def __init__(self):
        self.greenlets = []

    @staticmethod
    def sleep(time):
        gevent.sleep(time)

    @staticmethod
    def timer(callback, period):
        while True:
            callback()
            gevent.sleep(period)

    @staticmethod
    def kill(greenlet):
        greenlet.kill()

    @staticmethod
    def join(greenlet):
        greenlet.join()

    def join_all(self):
        joined_greenlets = gevent.joinall(self.greenlets)
        self.greenlets = []
        return joined_greenlets

    def execute(self, fn, *args, **kwargs):
        greenlet = gevent.spawn(fn, *args, **kwargs)
        self.greenlets.append(greenlet)
        return greenlet
