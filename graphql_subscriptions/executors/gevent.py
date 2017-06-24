import gevent
from promise import Promise
from geventwebsocket import WebSocketApplication

from .utils import process


class GeventMixin(WebSocketApplication):
    pass


class GeventExecutor(object):

    def __init__(self):
        self.jobs = []

    def wait_until_finished(self):
        [j.join() for j in self.jobs]
        # gevent.joinall(self.jobs)
        self.jobs = []

    def execute(self, fn, *args, **kwargs):
        promise = Promise()
        job = gevent.spawn(process, promise, fn, args, kwargs)
        self.jobs.append(job)
        return promise
