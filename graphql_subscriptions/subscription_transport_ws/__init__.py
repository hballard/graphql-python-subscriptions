from .base import BaseSubscriptionServer
from ..executors.gevent import GeventMixin
from ..executors.asyncio import AsyncioMixin


class GeventSubscriptionServer(BaseSubscriptionServer, GeventMixin):
    pass


class AsyncioSubscriptionServer(BaseSubscriptionServer, AsyncioMixin):
    pass


class SubscriptionServer(GeventSubscriptionServer):
    pass
