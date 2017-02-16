# graphql-python-subscriptions
A port of apollographql subscriptions for python, using gevent websockets and redis

This is a implementation of apollographql subscriptions-transport-ws and graphql-subscriptions in Python. It currently implements a pubsub using redis.py and gevent for concurrency.  It also makes heavy uses of syrusakbary/promise python implementation to mirror the logic in the apollographql libraries.
