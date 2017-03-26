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

