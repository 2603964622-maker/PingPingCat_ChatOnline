from exts import db
from datetime import datetime, timezone


class Feedback(db.Model):
    """用户想法 / 意见反馈：前台用户写给管理端的内容（文字必填，可附图片），
    管理端在 /admin 看板查看并可回复，回复后写入站内通知告知提交人。"""
    __tablename__ = 'feedbacks'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='反馈ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='提交人用户ID')
    category = db.Column(db.String(16), default='idea', comment='类型 idea=想法 suggestion=建议 bug=问题 other=其他')
    content = db.Column(db.Text, nullable=False, comment='正文描述（必填）')
    images = db.Column(db.Text, default=None, comment='图片URL列表，JSON数组字符串（可选）')
    status = db.Column(db.SmallInteger, default=0, comment='0=待处理 1=已回复 2=已归档')
    reply = db.Column(db.Text, default=None, comment='管理员回复内容')
    replied_by = db.Column(db.Integer, db.ForeignKey('admins.id'), default=None, comment='回复人管理员ID')
    replied_at = db.Column(db.DateTime, default=None, comment='回复时间 UTC')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), comment='提交时间')

    __table_args__ = (
        db.Index('ix_feedback_user', 'user_id', 'created_at'),
        db.Index('ix_feedback_status', 'status', 'created_at'),
    )

    def __repr__(self):
        return f"<Feedback {self.category} by={self.user_id}>"
