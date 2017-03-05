graphql-python-subscriptions
============================

A port of apollographql subscriptions for python, using gevent
websockets and redis

This is a implementation of apollographql subscriptions-transport-ws and
graphql-subscriptions in Python. It currently implements a pubsub using
redis.py and uses gevent for concurrency. It also makes heavy use of
syrusakbary/promise python implementation to mirror the logic in the
apollo-graphql libraries.

Meant to be used in conjunction with graphql-python / graphene server
and apollo-graphql client.

Very initial implementation. Currently only works with Python 2. No
tests yet.

Installation
------------

::

    $ pip install graphql-subscriptions
