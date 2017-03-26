from . import db


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True)
    email = db.Column(db.String(120), unique=True)
    active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return '<User {}>'.format(self.username)

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_text = db.Column(db.String())
    date_time = db.Column(db.DateTime(), server_default=db.func.now())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User',
                           backref=db.backref('messages', lazy='dynamic'))

    def __repr__(self):
        return '<Message {}>'.format(self.id)

    def as_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
