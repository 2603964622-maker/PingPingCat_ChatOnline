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
    background = db.Column(db.String(500), default=None, comment='个人资料页头部背景图')
    theme = db.Column(db.String(10), default='dark', comment='外观模式 light/dark（默认深色）')
    global_bg = db.Column(db.String(500), default=None, comment='全局背景图（寄生于两种模式下）')
    bg_opacity = db.Column(db.Integer, default=55, comment='全局背景磨砂层不透明度 10-90（%）')
    _password = db.Column(db.String(255), default=None, comment='注册后输入密码') #密码
    Signature = db.Column(db.String(150),default='',nullable=True,comment="个性签名")
    gender = db.Column(db.SmallInteger, default=0, server_default='0', nullable=False, comment='性别 0=未知 1=男 2=女')
    profile_visibility = db.Column(db.SmallInteger, default=1, server_default='1', nullable=False, comment='资料卡“我的动态”可见范围 1=所有人 2=仅好友 3=仅自己（基础信息始终展示）')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=封禁')
    banned_until = db.Column(db.DateTime, default=None, comment='封禁截止时间 UTC；为空且 status=0 表示永久封禁，到期由钩子自动解封')
    muted_until = db.Column(db.DateTime, default=None, comment='全局禁言截止时间 UTC（敏感词违规累计触发），到期前不可发送任何消息')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc))
    last_online = db.Column(db.DateTime, default=None, comment='最近一次上线（连接）时间，UTC')
    deleted_at = db.Column(db.DateTime, default=None) # 软删除标记,不为NULL代表已注销 ,注销后不得物理删库，只是标记,聊天记录还可以关联

    # 关系
    sent_messages = db.relationship('Message', back_populates='sender', lazy='dynamic', foreign_keys='Message.sender_id')
    owned_groups = db.relationship('Group', back_populates='owner', lazy='dynamic')
    channel_memberships = db.relationship('ChannelMember', back_populates='user', lazy='dynamic')

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


class UserOnlineLog(db.Model):
    """在线会话流水：用户所有连接断开（真正离线）时关闭一条记录。
    logout_at 为 NULL 表示尚未关闭（服务异常重启可能遗留，统计时忽略）。"""
    __tablename__ = 'user_online_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True, comment='用户ID')
    login_at = db.Column(db.DateTime, nullable=False, comment='上线时间 UTC（连接数 0→1）')
    logout_at = db.Column(db.DateTime, default=None, comment='离线时间 UTC（连接数归零）；NULL=未关闭')
    duration_seconds = db.Column(db.Integer, default=None, comment='本次在线秒数，关闭时计算')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<UserOnlineLog user={self.user_id} {self.login_at}>"
