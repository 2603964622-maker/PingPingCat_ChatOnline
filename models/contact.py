from exts import db
from datetime import datetime,timezone
"""
Contact-ORM：好友/通讯录
Blacklist-ORM:；拉黑列表

"""
class Contact(db.Model):
    __tablename__ = 'contacts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID')
    friend_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='好友ID')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=删除')
    remark = db.Column(db.String(64), default=None, comment='备注名')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='添加时间')

    # 1. 在SQLAlchemy中，"__table_args__" 用来定义表级别的额外参数(比如索引、约束等)，作用在当前表上
    # 2. UniqueConstraint('user_id','friend_id'):建立一个联合约束,意思是这两个字段组合必须是唯一的
    #    防止用户重复添加好友
    __table_args__ = (
    #   db.UniqueConstraint(*columns,name="None")
    #               *columns:参与联合唯一的列名，可以传1个或者多个
    #               name="":设置约束的名称(强烈建议显示命名,方便数据库迁移和报错排查)
        db.UniqueConstraint('user_id', 'friend_id', name='uk_contact'),
    )

    def __repr__(self):
        return f"<Contact {self.user_id}->{self.friend_id}>"


class Blacklist(db.Model):
    __tablename__ = 'blacklists'

    id = db.Column(db.Integer, primary_key=True, comment='拉黑名单编号')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='拉黑者')
    blocked_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='被拉黑者')
    reason = db.Column(db.String(200), default=None, comment='拉黑原因')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='拉黑时间')

    def __repr__(self):
        return f"<Blacklist {self.user_id} blocks {self.blocked_id}>"