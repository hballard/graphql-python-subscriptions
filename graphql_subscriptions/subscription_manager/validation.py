from graphql import GraphQLError
from graphql.validation.rules.base import ValidationRule

FIELD = 'Field'

# XXX from Apollo pacakge: Temporarily use this validation
# rule to make our life a bit easier.


class SubscriptionHasSingleRootField(ValidationRule):
    __slots__ = 'field',

    def __init__(self, context):
        self.field = FIELD
        super(SubscriptionHasSingleRootField, self).__init__(context)

    def enter_OperationDefinition(self, node, key, parent, path, ancestors):
        schema = self.context.get_schema()
        schema.get_subscription_type()
        operation_name = node.name.value if node.name else ''
        num_fields = 0
        for selection in node.selection_set.selections:
            # TODO: Fix this string assertion of "Field"...Apollo did this
            # in their package and since I was porting, just followed the same
            if type(selection).__name__ == self.field:
                num_fields += 1
            else:
                self.context.report_error(
                    GraphQLError(
                        'Apollo subscriptions do not support fragments on\
 the root field', [node]))
        if num_fields > 1:
            self.context.report_error(
                GraphQLError(
                    self.too_many_subscription_fields_error(operation_name),
                    [node]))
        return False

    @staticmethod
    def too_many_subscription_fields_error(subscription_name):
        return 'Subscription "{0}" must have only one\
 field.'.format(subscription_name)
