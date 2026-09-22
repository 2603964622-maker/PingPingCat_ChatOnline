from enum import Enum

from sqlalchemy import false

from exts import db
from datetime import datetime,timezone

class GroupRoleEnum(Enum):
    OWNER = "群主"
    MANAGER = "管理员"
    USER = "普通用户"

class GroupRole(db.Model):
    __tablename__ = 'group_roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30),nullable=false,unique=True)
    display_name = db.Column(db.String(30))  #用于存权限枚举的中文

class Group(db.Model):
    __tablename__ = 'groups'

    id = db.Column(db.Integer, primary_key=True, comment='群聊ID')
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='群主ID')
    name = db.Column(db.String(64), nullable=False, comment='群名称')
    avatar = db.Column(db.String(500), default=None, comment='群头像')
    description = db.Column(db.String(200), default=None, comment='群简介')
    max_members = db.Column(db.Integer, default=200, comment='最大人数')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=解散')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='创建时间')

    # 关系已在 User 中通过 backref 建立，此处可省略
    members = db.relationship('GroupMember', backref='group', lazy='dynamic')
    messages = db.relationship('GroupMessage', backref='group', lazy='dynamic')

    def __repr__(self):
        return f"<Group {self.name} id={self.id}>"


class GroupMember(db.Model):
    __tablename__ = 'group_members'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    role = db.Column(db.SmallInteger, default=2, comment='1=群主 2=管理员 3=普通成员')
    nickname = db.Column(db.String(64), default=None, comment='群内昵称')
    mute_until = db.Column(db.DateTime, default=None, comment='禁言截止时间')
    joined_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='加入时间')

    __table_args__ = (
        db.UniqueConstraint('group_id', 'user_id', name='uk_group_member'),
    )

    def __repr__(self):
        return f"<GroupMember g={self.group_id} u={self.user_id}>"


class GroupMessage(db.Model):
    __tablename__ = 'group_messages'

    id = db.Column(db.Integer, primary_key=True, comment='群内消息编号')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    content = db.Column(db.Text, nullable=False, comment='消息内容')
    msg_type = db.Column(db.SmallInteger, default=1, comment='1=文本 2=图片 3=文件')
    deleted_at = db.Column(db.DateTime, default=None, comment='软删除标记')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')

    def __repr__(self):
        return f"<GroupMessage {self.id} g={self.group_id}>"


class GroupAnnouncement(db.Model):
    __tablename__ = 'group_announcements'

    id = db.Column(db.Integer, primary_key=True, comment='公告ID')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    publisher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发布者')
    content = db.Column(db.Text, nullable=False, comment='公告内容')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发布时间')

    def __repr__(self):
        return f"<GroupAnnouncement g={self.group_id}>"