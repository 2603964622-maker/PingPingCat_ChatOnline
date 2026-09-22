from celery.worker.strategy import default

from exts import db
from datetime import datetime,timezone
from werkzeug.security import generate_password_hash, check_password_hash

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(32), unique=True, nullable=False, comment='唯一编号')
    email = db.Column(db.String(255), unique=True, nullable=False)
    nickname = db.Column(db.String(64), nullable=False) #昵称
    avatar = db.Column(db.String(500), default=None)  #头像路径
    _password = db.Column(db.String(255), default=None, comment='注册后输入密码') #密码
    Signature = db.Column(db.String(150),default='',nullable=True,comment="个性签名")
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=封禁')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc))
    deleted_at = db.Column(db.DateTime, default=None) # 软删除标记,不为NULL代表已注销 ,注销后不得物理删库，只是标记,聊天记录还可以关联

    # 关系
    sent_messages = db.relationship('Message', backref='sender', lazy='dynamic', foreign_keys='Message.sender_id')
    owned_groups = db.relationship('Group', backref='owner', lazy='dynamic')

    def __repr__(self):
        return f"<User {self.number}>"

    def __init__(self, *args,**kwargs):
        if "password" in kwargs:
            self.password = kwargs["password"]
            kwargs.pop("password")
        super().__init__(*args,**kwargs)

    @property
    def password(self):
        return self._password
    @password.setter
    def password(self,raw_password):
        self._password = generate_password_hash(raw_password)
    def check_password(self,raw_password):
        return check_password_hash(self._password,raw_password)
