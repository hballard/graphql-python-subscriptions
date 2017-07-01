from __future__ import absolute_import

from geventwebsocket.exceptions import WebSocketError
import gevent


class GeventExecutor(object):
    # used to patch socket library so it doesn't block
    socket = gevent.socket
    error = WebSocketError

    def __init__(self):
        self.greenlets = []

    @staticmethod
    def ws_close(ws, code):
        ws.close(code)

    @staticmethod
    def ws_protocol(ws):
        return ws.protocol

    @staticmethod
    def ws_open(ws):
        if ws.closed:
            return False
        else:
            return True

    @staticmethod
    def ws_send(ws, msg, **kwargs):
        ws.send(msg, **kwargs)

    @staticmethod
    def ws_recv(ws):
        return ws.receive()

    @staticmethod
    def sleep(time):
        gevent.sleep(time)

    @staticmethod
    def set_timeout(callback, period):
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
