import graphene
import graphene_sqlalchemy

from . import db, pubsub
from .models import Message as MessageModel
from .models import User as UserModel


class User(graphene_sqlalchemy.SQLAlchemyObjectType):

    class Meta:
        model = UserModel
        interfaces = (graphene.relay.Node, )


class Message(graphene_sqlalchemy.SQLAlchemyObjectType):

    class Meta:
        model = MessageModel
        interfaces = (graphene.relay.Node, )


class Query(graphene.ObjectType):
    node = graphene.relay.Node.Field()
    users = graphene_sqlalchemy.SQLAlchemyConnectionField(
        User,
        active=graphene.Boolean()
    )
    messages = graphene_sqlalchemy.SQLAlchemyConnectionField(Message)

    def resolve_users(self, args, context, info):
        query = User.get_query(context)
        if args.get('active') is None:
            return query
        return query.filter_by(active=args.get('active'))


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
        if pubsub.subscriptions:
            pubsub.publish('users', new_user.as_dict())
        return AddUser(ok=ok, user=new_user)


class EditUser(graphene.ClientIDMutation):

    class Input:
        id = graphene.ID(required=True)
        username = graphene.String()
        email = graphene.String()
        active = graphene.Boolean()

    ok = graphene.Boolean()
    user = graphene.Field(lambda: User)

    @classmethod
    def mutate_and_get_payload(cls, args, context, info):
        _input = args.copy()
        _type, _id = graphene.relay.node.from_global_id(_input.get('id'))
        _input['id'] = _id
        del _input['clientMutationId']
        UserModel.query.filter_by(id=_input.get('id')).update(_input)
        db.session.commit()
        ok = True
        user = UserModel.query.get(_input.get('id'))
        return EditUser(ok=ok, user=user)


class DeleteUser(graphene.ClientIDMutation):

    class Input:
        id = graphene.ID(required=True)

    ok = graphene.Boolean()
    user = graphene.Field(lambda: User)

    @classmethod
    def mutate_and_get_payload(cls, args, context, info):
        _input = args.copy()
        _type, _id = graphene.relay.node.from_global_id(_input.get('id'))
        _input['id'] = _id
        user = UserModel.query.get(_input.get('id'))
        db.session.delete(user)
        db.session.commit()
        ok = True
        return DeleteUser(ok=ok, user=user)


class AddMessage(graphene.ClientIDMutation):

    class Input:
        message_text = graphene.String(required=True)
        user_id = graphene.ID(required=True)

    ok = graphene.Boolean()
    message = graphene.Field(lambda: Message)

    @classmethod
    def mutate_and_get_payload(cls, args, context, info):
        _type, user_id = graphene.relay.node.from_global_id(
            args.get('user_id'))
        _input = args.copy()
        _input['user_id'] = user_id
        del _input['clientMutationId']
        new_message = MessageModel(**_input)
        db.session.add(new_message)
        db.session.commit()
        ok = True
        return AddMessage(ok=ok, message=new_message)


class Mutation(graphene.ObjectType):

    add_user = AddUser.Field()
    edit_user = EditUser.Field()
    delete_user = DeleteUser.Field()
    add_message = AddMessage.Field()


class Subscription(graphene.ObjectType):
    users = graphene_sqlalchemy.SQLAlchemyConnectionField(
        User,
        active=graphene.Boolean()
    )

    def resolve_users(self, args, context, info):
        query = User.get_query(context)
        # if args.get('active') is None:
        return query.filter_by(id=info.root_value.get('id'))
        # elif args.get('active') == info.root_value.get('active'):
            # return query.filter_by(id=info.root_value.get('id'))
        # else:
            # return None


schema = graphene.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription
)
