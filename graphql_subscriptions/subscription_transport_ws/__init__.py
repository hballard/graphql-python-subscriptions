from .base import BaseSubscriptionServer
from ..executors.gevent import GeventMixin


class SubscriptionServer(BaseSubscriptionServer, GeventMixin):
    pass
