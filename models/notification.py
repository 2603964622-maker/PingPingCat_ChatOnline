from exts import db
from datetime import datetime,timezone


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True, comment='通知ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='接收者ID')
    type = db.Column(db.String(30), nullable=False, comment='类型(mention/reply/like/post/system)')
    title = db.Column(db.String(100), default=None, comment='通知标题')
    content = db.Column(db.String(500), default=None, comment='通知内容')
    related_id = db.Column(db.Integer, default=None, comment='关联业务ID')
    related_type = db.Column(db.String(30), default=None, comment='关联业务类型')
    is_read = db.Column(db.SmallInteger, default=0, comment='0=未读 1=已读')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='创建时间')

    def __repr__(self):
        return f"<Notification {self.type} to={self.user_id}>"