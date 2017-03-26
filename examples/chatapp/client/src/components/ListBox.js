import React from 'react'

const ListBox = ({data: {users, loading}}) => {
  if (loading) {
    return <div>
             <h3>Loading...</h3>
           </div>
  } else {
    return (
      <div>
        <ul>
          {users.edges.map((element) => (
             <li key={element.node.id}>
               {element.node.username}
             </li>
           ))}
        </ul>
      </div>
    )
  }
}

ListBox.propTypes = {data: React.PropTypes.object}

export default ListBox
