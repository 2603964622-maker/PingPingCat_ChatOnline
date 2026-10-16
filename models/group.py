from enum import Enum

from exts import db
from datetime import datetime,timezone

class GroupRoleEnum(Enum):
    OWNER = "群主"
    MANAGER = "管理员"
    USER = "普通用户"

class GroupRole(db.Model):
    __tablename__ = 'group_roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30),nullable=False,unique=True)
    display_name = db.Column(db.String(30))  #用于存权限枚举的中文
    # 说明：本表仅作角色字典（commands.py 初始化写入）；成员权限由 GroupMember.role 小整数(1/2/3)判断，
    # 两表之间没有外键，不要在此声明 relationship，否则 SQLAlchemy 无法推导 join 条件会直接报错

class Group(db.Model):
    __tablename__ = 'groups'

    id = db.Column(db.Integer, primary_key=True, comment='群聊ID',autoincrement=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='群主ID')
    number = db.Column(db.String(20),nullable=False,unique=True)
    name = db.Column(db.String(64), nullable=False, comment='群名称')
    avatar = db.Column(db.String(500), default=None, comment='群头像')
    description = db.Column(db.String(200), default=None, comment='群简介')
    max_members = db.Column(db.Integer, default=200, comment='最大人数')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=解散')
    mute_all = db.Column(db.SmallInteger, default=0, comment='0=正常 1=全员禁言')
    join_need_verify = db.Column(db.SmallInteger, default=1, comment='入群方式：1=需验证审核 0=直接加入')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='创建时间')

    owner = db.relationship('User', back_populates='owned_groups', foreign_keys=[owner_id])
    group_files = db.relationship('GroupFile', back_populates='group', lazy='joined')

    # 关系
    members = db.relationship('GroupMember', back_populates='group', lazy='dynamic')
    messages = db.relationship('GroupMessage', back_populates='group', lazy='dynamic')

    def __repr__(self):
        return f"<Group {self.name} id={self.id}>"


class GroupMember(db.Model):
    __tablename__ = 'group_members'

    id = db.Column(db.Integer, primary_key=True,autoincrement=True)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    role = db.Column(db.SmallInteger, default=3, comment='1=群主 2=管理员 3=普通成员')
    nickname = db.Column(db.String(64), default=None, comment='群内昵称')
    mute_until = db.Column(db.DateTime, default=None, comment='禁言截止时间')
    joined_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='加入时间')

    __table_args__ = (
        db.UniqueConstraint('group_id', 'user_id', name='uk_group_member'),
    )

    group = db.relationship('Group', back_populates='members')

    def __repr__(self):
        return f"<GroupMember g={self.group_id} u={self.user_id}>"


class GroupMessage(db.Model):
    __tablename__ = 'group_messages'

    id = db.Column(db.Integer, primary_key=True, comment='群内消息编号',autoincrement=True)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    content = db.Column(db.Text, nullable=False, comment='消息内容')
    msg_type = db.Column(db.SmallInteger, default=1, comment='1=文本 2=图片 3=文件')
    mentions = db.Column(db.String(255), default=None, comment='被@用户ID列表，逗号分隔')
    reply_to = db.Column(db.Integer, default=None, comment='引用回复的消息ID(指向本群内另一条 group_messages.id)，空=非回复')
    deleted_at = db.Column(db.DateTime, default=None, comment='软删除标记')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')

    group = db.relationship('Group', back_populates='messages')
    sender = db.relationship('User', foreign_keys=[sender_id])

    def __repr__(self):
        return f"<GroupMessage {self.id} g={self.group_id}>"


class GroupInvite(db.Model):
    """群聊邀请表：群成员邀请某用户入群，需被邀请人确认后才正式加入"""
    __tablename__ = 'group_invites'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='邀请编号')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    inviter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='邀请人ID')
    invitee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='被邀请人ID')
    status = db.Column(db.SmallInteger, default=0, comment='0=待处理 1=已同意 2=已拒绝')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发起时间')
    handled_at = db.Column(db.DateTime, default=None, comment='处理时间')

    __table_args__ = (
        # 同 group_owner_transfers：不能对 (group_id, invitee_id, status) 建唯一约束，
        # 否则同一用户被多次拒绝/接受会撞唯一键；待处理去重交由应用层检查
        db.Index('ix_group_invite_group_invitee', 'group_id', 'invitee_id', 'status'),
    )

    def __repr__(self):
        return f"<GroupInvite g={self.group_id} u={self.invitee_id} status={self.status}>"


class GroupOwnerTransfer(db.Model):
    """群主转让表：群主发起转让后需被转让人确认，同意后才正式变更角色"""
    __tablename__ = 'group_owner_transfers'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='转让申请编号')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    from_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发起群主ID')
    to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='被转让成员ID')
    status = db.Column(db.SmallInteger, default=0, comment='0=待处理 1=已同意 2=已拒绝')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发起时间')
    handled_at = db.Column(db.DateTime, default=None, comment='处理时间')

    __table_args__ = (
        # 不能用含 status 的唯一约束：否则同一成员第二次进入终态（再次拒绝/接受）会撞键
        # 待处理去重由应用层 filter_by(status=0) 保证，DB 层仅建普通索引加速查询
        db.Index('ix_group_transfer_group_to', 'group_id', 'to_id', 'status'),
    )

    def __repr__(self):
        return f"<GroupOwnerTransfer g={self.group_id} to={self.to_id} status={self.status}>"


class GroupJoinRequest(db.Model):
    """入群申请表：用户通过搜索/编号自助申请加入群聊，需群主或管理员审核通过后才正式入群"""
    __tablename__ = 'group_join_requests'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='申请编号')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='申请人ID')
    message = db.Column(db.String(200), default=None, comment='验证消息')
    status = db.Column(db.SmallInteger, default=0, comment='0=待处理 1=已通过 2=已拒绝')
    handled_by = db.Column(db.Integer, db.ForeignKey('users.id'), default=None, comment='处理人ID')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发起时间')
    handled_at = db.Column(db.DateTime, default=None, comment='处理时间')

    __table_args__ = (
        # 与 group_invites 同理：不能对含 status 的字段建唯一约束，否则再次申请/重复终态会撞键；
        # 待处理去重交由应用层 filter_by(status=0) 保证，DB 层仅建普通索引加速查询
        db.Index('ix_group_join_req_group_user', 'group_id', 'user_id', 'status'),
    )

    def __repr__(self):
        return f"<GroupJoinRequest g={self.group_id} u={self.user_id} status={self.status}>"


class GroupAnnouncement(db.Model):
    __tablename__ = 'group_announcements'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True,comment='公告ID')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    publisher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发布者')
    content = db.Column(db.Text, nullable=False, comment='公告内容')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发布时间')

    def __repr__(self):
        return f"<GroupAnnouncement g={self.group_id}>"

class AnnouncementUserIsRead(db.Model):
    """
    插入用户已读的记录 即可
    """
    __tablename__ = 'group_announcement_user_is_read'
    id = db.Column(db.Integer, primary_key=True,autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    announcement_id = db.Column(db.Integer, db.ForeignKey('group_announcements.id'), nullable=False)

    def __repr__(self):
        return f"<AnnouncementUserIsRead g={self.group_id}>"