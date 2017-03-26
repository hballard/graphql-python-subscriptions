import React from 'react'
import ReactDOM from 'react-dom'
import ApolloClient, { createNetworkInterface } from 'apollo-client'
import { SubscriptionClient, addGraphQLSubscriptions } from 'subscriptions-transport-ws'
import { ApolloProvider } from 'react-apollo'

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
