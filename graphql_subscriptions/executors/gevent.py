from __future__ import absolute_import

from geventwebsocket.exceptions import WebSocketError
import gevent


class GeventExecutor(object):
    # used to patch socket library so it doesn't block
    socket = gevent.socket
    error = WebSocketError

    def __init__(self):
        self.greenlets = []

    def ws_close(self, code):
        self.ws.close(code)

    def ws_protocol(self):
        return self.ws.protocol

    def ws_isopen(self):
        if self.ws.closed:
            return False
        else:
            return True

    def ws_send(self, msg, **kwargs):
        self.ws.send(msg, **kwargs)

    def ws_recv(self):
        return self.ws.receive()

    @staticmethod
    def sleep(time):
        gevent.sleep(time)

    @staticmethod
    def kill(greenlet):
        gevent.kill(greenlet)

    @staticmethod
    def join(greenlet, timeout=None):
        greenlet.join(timeout)

    def join_all(self):
        gevent.joinall(self.greenlets)
        self.greenlets = []

    def execute(self, fn, *args, **kwargs):
        greenlet = gevent.spawn(fn, *args, **kwargs)
        self.greenlets.append(greenlet)
        return greenlet
