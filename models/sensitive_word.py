from exts import db
from datetime import datetime,timezone


class SensitiveWord(db.Model):
    __tablename__ = 'sensitive_words'

    id = db.Column(db.Integer, primary_key=True, comment='敏感词ID')
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