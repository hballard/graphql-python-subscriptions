# graphql-python-subscriptions
#### (Work in Progress!)
A port of apollographql subscriptions for python, using gevent websockets and redis

This is an implementation of graphql subscriptions in Python.  It uses the apollographql  [subscriptions-transport-ws](https://github.com/apollographql/subscriptions-transport-ws) and [graphql-subscriptions](https://github.com/apollographql/graphql-subscriptions) packages as its basis.  It currently implements a pubsub using [redis-py](https://github.com/andymccurdy/redis-py) and uses [gevent-websockets](https://bitbucket.org/noppo/gevent-websocket) for concurrency.  It also makes heavy use of [syrusakbary/promise](https://github.com/syrusakbary/promise) python implementation to mirror the logic in the apollo-graphql libraries.

Meant to be used in conjunction with [graphql-python](https://github.com/graphql-python) / [graphene](http://graphene-python.org/) server and [apollo-client](http://dev.apollodata.com/) for graphql.  The api is below, but if you want more information, consult the apollo graphql libraries referenced above, and specifcally as it relates to using their graphql subscriptions client.

Initial implementation.  Good test coverage.  Works with both Python 2 / 3.

## Installation
```
$ pip install graphql-subscriptions
```

## API
### RedisPubsub(host='localhost', port=6379, \*args, **kwargs)
#### Arguments
- `host`: Redis server instance url or IP (optional)
- `port`: Redis server port (optional)
- `args, kwargs`: Any additional position and keyword args will be passed to Redis-py constructor (optional)

#### Methods
- `publish(trigger_name, message)`: Trigger name is a subscription or pubsub channel; message is the mutation object or message that will end up being passed to the subscription as the root_value; this method should be called inside of mutation resolve function
- `subscribe(trigger_name, on_message_handler, options)`: Trigger name is a subscription or pubsub channel; on_message_handler is the callback that will be triggered on each mutation; this method is called by the subscription manager
- `unsubscribe(sub_id)`: Sub_id is the subscription ID that is being tracked by the pubsub instance -- it is returned from the `subscribe` method and called by the subscription manager
- `wait_and_get_message()`: Called by the `subscribe` method during the first subscription for the server; run in a separate greenlet and calls Redis `get_message()` method to constantly poll for new messages on pubsub channels
- `handle_message(message)`: Called by the pubsub when a message is received on a subscribed channel; will check all existing pubsub subscriptons and then calls `on_message_handler()` for all matches

### SubscriptionManager(schema, pubsub, setup_funcs={})
#### Arguments
- `schema`: Graphql schema instance (required)
- `pubsub`: Any pubsub instance with publish, subscribe, and unsubscribe methods (in this case an instance of the RedisPubsub class) (required)
- `setup_funcs`: Dictionary of setup functions that map from subscription name to a map of pubsub channel names and their filter functions; kwargs parameters are: `query, operation_name, callback, variables, context, format_error, format_response, args, subscription_name` (optional)

  example:
  ```python
  def new_user(**kwargs):
      args = kwargs.get('args')
      return {
          'new_user_channel': {
              'filter': lambda root, context: root.active == args.active
          }
      }

  setup_funcs = {'new_user': new_user}
  ```

#### Methods
- `publish(trigger_name, payload)`: Trigger name is the subscription or pubsub channel; payload is the mutation object or message that will end up being passed to the subscription root_value; method called inside of mutation resolve function
- `subscribe(query, operation_name, callback, variables, context, format_error, format_response)`: Called by SubscriptionServer upon receiving a new subscription from a websocket.  Arguments are parsed by SubscriptionServer from the graphql subscription query
- `unsubscribe(sub_id)`: Sub_id is the subscription ID that is being tracked by the subscription manager instance -- returned from the `subscribe()` method and called by the SubscriptionServer

### SubscriptionServer(subscription_manager, websocket, keep_alive=None, on_subscribe=None, on_unsubscribe=None, on_connect=None, on_disconnect=None)
#### Arguments
- `subscription_manager`: A subscripton manager instance (required).
- `websocket`: The websocket object passed in from your route handler (required).
- `keep_alive`: The time period, in seconds, that the server will send keep alive messages to the client. (optional)
- `on_subscribe(message, subscription_parameters, websocket)`: Function to create custom params that will be used when resolving this subscription (optional)
- `on_unsubscribe(websocket)`: Function that is called when a client unsubscribes (optional)
- `on_connect(message_payload, websocket)`: Function that will be called when a client connects to the socket, called with the message_payload from the client, if the return value is an object, its elements will be added to the context.  Return false or throw an exception to reject the connection.  May return a Promise. (optional)
- `on_disconnect(websocket)`: Function that called when a client disconnects (optional)

#### Methods
- `on_open()`: Called when the socket first opens; checks for correct subscription protocol and initializes keep alive messages
- `on_close(reason)`: Called when socket is closed; unsubscribes from subscriptions and deletes subscription objects
- `on_message(message)`: provides main control flow for all messaging exchanged on the socket between server and client; parses initial message, checks for exceptions, responds to client and subscribes / unsubscribes socket to mutation channels, via pubsub
- `unsubscribe(sub_id)`: Unsubscribes socket from subscriptions specified by client
- `timer()`: Timer for sending keep alive messages to client; run in separate greenlet per socket
- `send_init_result(result), send_keep_alive(), send_subscription_data(sub_id, payload), send_subscription_fail(sub_id, payload), send_subscription_success(sub_id)`: convenience methods for sending different messages and payloads to client

## Example Usage
#### Server (using Flask and Flask-Sockets):

```python
from flask import Flask
from flask_sockets import Sockets
from graphql_subscriptions import (
    SubscriptionManager,
    RedisPubsub,
    SubscriptionServer
)

app = Flask(__name__)

# using Flask Sockets here, but could use gevent-websocket directly
# to create a websocket app and attach it to flask app object
sockets = Sockets(app)

# instantiate pubsub -- this will be used to "publish" mutations
# and also to pass it into your subscription manager
pubsub = RedisPubsub()

# create schema using graphene or another python graphql library
# not showing models or schema design here for brevity
schema = graphene.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription
)

# instantiate subscription manager object -- passing in schema and pubsub
subscription_mgr = SubscriptionManager(schema, pubsub)

# using Flask Sockets here -- on each new connection instantiate a
# subscription app / server -- passing in subscription manager and websocket
@sockets.route('/socket')
def socket_channel(websocket):
    subscription_server = SubscriptionServer(subscription_mgr, websocket)
    subscription_server.handle()
    return []

if __name__ == "__main__":

    # using a gevent webserver so multiple connections can be
    # maintained concurrently -- gevent websocket spawns a new
    # greenlet for each request and forwards the request to flask
    # app or socket app, depending on request type
    from geventwebsocket import WebSocketServer

    server = WebSocketServer(('', 5000), app)
    print '  Serving at host 0.0.0.0:5000...\n'
    server.serve_forever()
```

Of course on the server you have to "publish" each time you have a mutation (in this case to a redis channel).  That would look something like this (using graphene / sql-alchemy):


```python
class AddUser(graphene.ClientIDMutation):

    class Input:
        username = graphene.String(required=True)
        email = graphene.String()

    ok = graphene.Boolean()
    user = graphene.Field(lambda: User)

    @classmethod
    def mutate_and_get_payload(cls, args, context, info):
        _input = args.copy()
        del _input['clientMutationId']
        new_user = UserModel(**_input)
        db.session.add(new_user)
        db.session.commit()
        ok = True
        # publish result of mutation to pubsub; check to see if there are any
        # active subscriptions first; this implementation uses cPickle to serialize,
        # so you could send regular python object; here I'm converting to a dict before
        # publishing
        if pubsub.subscriptions:
            pubsub.publish('users', new_user.as_dict())
        return AddUser(ok=ok, user=new_user)

class Subscription(graphene.ObjectType):
    users = graphene_sqlalchemy.SQLAlchemyConnectionField(
        User,
        active=graphene.Boolean()
    )

    # mutation oject that was published will be passed as
    # root_value of subscription
    def resolve_users(self, args, context, info):
        with app.app_context():
            query = User.get_query(context)
            return query.filter_by(id=info.root_value.get('id'))
```

#### Client (using Apollo Client library):
First create create network interface and and client instances and then wrap them in a subscription client instance
```js
import ReactDOM from 'react-dom'
import { ApolloProvider } from 'react-apollo'
import ApolloClient, { createNetworkInterface } from 'apollo-client'
import { SubscriptionClient, addGraphQLSubscriptions } from 'subscriptions-transport-ws'

import ChatApp from './screens/ChatApp'

const networkInterface = createNetworkInterface({
  uri: 'http://localhost:5000/graphql'
})

const wsClient = new SubscriptionClient(`ws://localhost:5000/socket`, {
  reconnect: true
})

const networkInterfaceWithSubscriptions = addGraphQLSubscriptions(
  networkInterface,
  wsClient,
)

const client = new ApolloClient({
  dataIdFromObject: o => o.id,
  networkInterface: networkInterfaceWithSubscriptions
})

ReactDOM.render(
  <ApolloProvider client={client}>
    <ChatApp />
  </ApolloProvider>,
  document.getElementById('root')
)
```
Build a simple component and then call subscribeToMore method on the returned data object from the inital graphql query
```js

import React from 'react'
import { graphql } from 'react-apollo'
import gql from 'graphql-tag'
import ListBox from '../components/ListBox'

const SUBSCRIPTION_QUERY = gql`
  subscription newUsers {
    users(active: true) {
      edges {
        node {
          id
          username
        }
      }
    }
  }
`

const LIST_BOX_QUERY = gql`
  query AllUsers {
    users(active: true) {
      edges {
        node {
          id
          username
        }
      }
    }
  }
`

class ChatListBox extends React.Component {

  componentWillReceiveProps(newProps) {
    if (!newProps.data.loading) {
      if (this.subscription) {
        return
      }
      this.subscription = newProps.data.subscribeToMore({
        document: SUBSCRIPTION_QUERY,
        updateQuery: (previousResult, {subscriptionData}) => {
          const newUser = subscriptionData.data.users.edges
          const newResult = {
            users: {
              edges: [
                ...previousResult.users.edges,
                ...newUser
              ]
            }
          }
          return newResult
        },
        onError: (err) => console.error(err)
      })
    }
  }

  render() {
    return <ListBox data={this.props.data} />
  }
}

const ChatListBoxWithData = graphql(LIST_BOX_QUERY)(ChatListBox)

export default ChatListBoxWithData

```
