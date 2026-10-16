from exts import db
from datetime import datetime, timezone


class SiteAnnouncement(db.Model):
    """全站公告：管理端面向所有用户发布，可附图片/视频。
    发布后通过 SocketIO 广播 site_announcement 事件，在线用户立刻收到；
    离线用户下次进入时由 /announcement/pending 补发。"""
    __tablename__ = 'site_announcements'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='公告ID')
    title = db.Column(db.String(120), nullable=False, comment='公告标题')
    content = db.Column(db.Text, default=None, comment='公告正文')
    media = db.Column(db.Text, default=None, comment='图片/视频列表，JSON数组字符串 [{type:2视频|3图片,url,name}]')
    publisher_id = db.Column(db.Integer, db.ForeignKey('admins.id'), default=None, comment='发布人管理员ID')
    status = db.Column(db.SmallInteger, default=1, comment='1=发布中 0=已下架')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), comment='发布时间')

    __table_args__ = (
        db.Index('ix_site_ann_status', 'status', 'created_at'),
    )

    def __repr__(self):
        return f"<SiteAnnouncement #{self.id} {self.title}>"


class SiteAnnouncementRead(db.Model):
    """用户对全站公告的“已知晓”记录：只有点了已知晓才算读达，点弹窗叉号不写入。"""
    __tablename__ = 'site_announcement_reads'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='记录ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    announcement_id = db.Column(db.Integer, db.ForeignKey('site_announcements.id'), nullable=False, comment='公告ID')
    read_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), comment='知晓时间')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'announcement_id', name='uq_site_ann_read'),
    )

    def __repr__(self):
        return f"<SiteAnnouncementRead u={self.user_id} a={self.announcement_id}>"
