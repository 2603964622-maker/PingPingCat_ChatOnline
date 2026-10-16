from exts import db
from datetime import datetime,timezone


class SensitiveWord(db.Model):
    __tablename__ = 'sensitive_words'

    id = db.Column(db.Integer, primary_key=True, comment='敏感词ID', autoincrement=True, nullable=False)
    word = db.Column(db.String(100), nullable=False, comment='敏感词内容')
    level = db.Column(db.SmallInteger, default=1, comment='处理级别(1=警告 2=删除 3=禁言 4=踢出)')
    category = db.Column(db.String(50), default='default', comment='分类')
    is_active = db.Column(db.SmallInteger, default=1, comment='1=启用 0=禁用')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='创建时间')

    __table_args__ = (
        db.UniqueConstraint('word', name='uk_word'),
    )

    def __repr__(self):
        return f"<SensitiveWord {self.word}>"


class SensitiveViolation(db.Model):
    """敏感词命中流水：每拦截一次写一条，用于累计违规次数触发禁言/踢出"""
    __tablename__ = 'sensitive_violations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='记录ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True, comment='违规用户')
    scene = db.Column(db.String(20), default='private', comment='命中场景 private/group/channel/moment')
    words = db.Column(db.String(255), default=None, comment='命中的敏感词（逗号分隔）')
    level = db.Column(db.SmallInteger, default=1, comment='本次命中最高级别')
    group_id = db.Column(db.Integer, default=None, comment='群场景下该群 id')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), comment='命中时间 UTC')

    def __repr__(self):
        return f"<SensitiveViolation user={self.user_id} {self.words}>"