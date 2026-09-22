from exts import db
from datetime import datetime,timezone
from enum import Enum
"""
    Channel-ORM:频道相关信息
    ChannelMember-ORM:频道成员表
    ChannelPost-ORM:频道帖子表
    ChannelComment-ORM:频道评论表
    ChannelLike-ORM:频道帖子点赞表
    ChannelAnnouncement-ORM:频道公告表
"""
class ChannelRoleEnum(Enum):
    OWNER = "频道主"
    VICE_OWNER = "副频道主"
    SUBSCRIBER = "订阅者"

class ChannelRole(db.Model):
    __tablename__ = 'channel_roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30),nullable=False,unique=True)
    display_name = db.Column(db.String(30),nullable=False,unique=True)

class Channel(db.Model):
    __tablename__ = 'channels'

    id = db.Column(db.Integer, primary_key=True, comment='频道ID')
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='创建者')
    name = db.Column(db.String(64), nullable=False, comment='频道名称')
    description = db.Column(db.String(200), default=None, comment='频道描述')
    avatar = db.Column(db.String(500), default=None, comment='频道图标')
    is_public = db.Column(db.SmallInteger, default=1, comment='1=公开 0=私有')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=封禁')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='创建时间')

    members = db.relationship('ChannelMember', backref='channel', lazy='dynamic')
    posts = db.relationship('ChannelPost', backref='channel', lazy='dynamic')

    def __repr__(self):
        return f"<Channel {self.name} id={self.id}>"


class ChannelMember(db.Model):
    __tablename__ = 'channel_members'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False, comment='频道ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    role = db.Column(db.SmallInteger, default=3, comment='1=主理人 2=副主理人 3=订阅者')
    notify_level = db.Column(db.SmallInteger, default=1, comment='1=全部 2=仅@ 3=静默')
    joined_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='加入时间')

    __table_args__ = (
        db.UniqueConstraint('channel_id', 'user_id', name='uk_channel_member'),
    )

    def __repr__(self):
        return f"<ChannelMember c={self.channel_id} u={self.user_id}>"


class ChannelPost(db.Model):
    __tablename__ = 'channel_posts'

    id = db.Column(db.Integer, primary_key=True, comment='帖子ID')
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False, comment='频道ID')
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='作者ID')
    title = db.Column(db.String(100), default=None, comment='标题')
    content = db.Column(db.Text, nullable=False, comment='正文')
    view_count = db.Column(db.Integer, default=0, comment='浏览量')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=删除')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发布时间')

    comments = db.relationship('ChannelComment', backref='post', lazy='dynamic')
    likes = db.relationship('ChannelLike', backref='post', lazy='dynamic')

    def __repr__(self):
        return f"<ChannelPost {self.id} c={self.channel_id}>"


class ChannelComment(db.Model):
    __tablename__ = 'channel_comments'

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('channel_posts.id'), nullable=False, comment='帖子ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='评论者')
    content = db.Column(db.String(500), nullable=False, comment='评论内容')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='评论时间')

    def __repr__(self):
        return f"<ChannelComment p={self.post_id}>"


class ChannelLike(db.Model):
    __tablename__ = 'channel_likes'

    id = db.Column(db.Integer, primary_key=True, comment='点赞ID')
    post_id = db.Column(db.Integer, db.ForeignKey('channel_posts.id'), nullable=False, comment='帖子ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='点赞时间')

    __table_args__ = (
        db.UniqueConstraint('post_id', 'user_id', name='uk_post_like'),
    )

    def __repr__(self):
        return f"<ChannelLike p={self.post_id} u={self.user_id}>"


class ChannelAnnouncement(db.Model):
    __tablename__ = 'channel_announcements'

    id = db.Column(db.Integer, primary_key=True, comment='公告ID')
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False, comment='所属频道ID')
    publisher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发布者ID')
    title = db.Column(db.String(200), nullable=False, comment='公告标题')
    content = db.Column(db.Text, nullable=False, comment='公告内容')
    cover_url = db.Column(db.String(500), default=None, comment='封面图URL')
    is_pinned = db.Column(db.SmallInteger, default=0, comment='1=置顶 0=普通')
    is_deleted = db.Column(db.SmallInteger, default=0, comment='1=已删除(软删除)')
    view_count = db.Column(db.Integer, default=0, comment='浏览次数')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发布时间')
    updated_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), onupdate=lambda:datetime.now(timezone.utc), comment='最后编辑时间')
    deleted_at = db.Column(db.DateTime, default=None, comment='删除时间')


    __table_args__ = (
        db.Index('idx_channel_announcements', 'channel_id', 'is_pinned', 'created_at'),
        {'comment': '频道公告表'}
    )

    def __repr__(self):
        return f"<ChannelAnnouncement {self.id} channel={self.channel_id} title={self.title}>"