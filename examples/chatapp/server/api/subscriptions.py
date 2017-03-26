from graphql_subscriptions import SubscriptionManager

from . import pubsub
from .schema import schema

subscription_mgr = SubscriptionManager(schema, pubsub)
