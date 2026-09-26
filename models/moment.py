from exts import db
from datetime import datetime,timezone
"""
    Moment-ORM:动态信息表
    MomentMedia-ORM:动态媒体表
    MomentLike-ORM:动态点赞表
    MomentComment-ORM:动态评论表
"""

class Moment(db.Model):
    __tablename__ = 'moments'

    id = db.Column(db.Integer, primary_key=True, comment='动态ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发布者')
    content = db.Column(db.Text, nullable=False, comment='文字内容')
    visibility = db.Column(db.SmallInteger, default=1, comment='1=公开 2=好友 3=仅自己')
    location = db.Column(db.String(100), default=None, comment='定位信息')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=删除')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发布时间')

    medias = db.relationship('MomentMedia', back_populates='moment', lazy='dynamic')
    likes = db.relationship('MomentLike', back_populates='moment', lazy='dynamic')
    comments = db.relationship('MomentComment', back_populates='moment', lazy='dynamic')

    def __repr__(self):
        return f"<Moment {self.id} u={self.user_id}>"


class MomentMedia(db.Model):
    __tablename__ = 'moment_medias'

    id = db.Column(db.Integer, primary_key=True, comment='媒体ID')
    moment_id = db.Column(db.Integer, db.ForeignKey('moments.id'), nullable=False, comment='动态ID')
    url = db.Column(db.String(500), nullable=False, comment='资源地址')
    media_type = db.Column(db.SmallInteger, default=1, comment='1=图片 2=视频')
    sort_order = db.Column(db.Integer, default=0, comment='排序')

    moment = db.relationship('Moment', back_populates='medias')

    def __repr__(self):
        return f"<MomentMedia m={self.moment_id}>"


class MomentLike(db.Model):
    __tablename__ = 'moment_likes'

    id = db.Column(db.Integer, primary_key=True, comment='点赞ID')
    moment_id = db.Column(db.Integer, db.ForeignKey('moments.id'), nullable=False, comment='动态ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='点赞时间')

    __table_args__ = (
        db.UniqueConstraint('moment_id', 'user_id', name='uk_moment_like'),
    )

    moment = db.relationship('Moment', back_populates='likes')

    def __repr__(self):
        return f"<MomentLike m={self.moment_id} u={self.user_id}>"


class MomentComment(db.Model):
    __tablename__ = 'moment_comments'

    id = db.Column(db.Integer, primary_key=True, comment='评论ID')
    moment_id = db.Column(db.Integer, db.ForeignKey('moments.id'), nullable=False, comment='动态ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='评论者')
    reply_to = db.Column(db.Integer, default=None, comment='回复的评论ID')
    content = db.Column(db.String(500), nullable=False, comment='评论内容')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='评论时间')

    moment = db.relationship('Moment', back_populates='comments')

    def __repr__(self):
        return f"<MomentComment m={self.moment_id}>"