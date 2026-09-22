from exts import db
from datetime import datetime,timezone
"""
friends-ORM：好友中间表（多对多关联，双向存储）
FriendRequest-ORM：好友申请表（申请→同意/拒绝流程）
Blacklist-ORM：拉黑列表
"""

# 好友中间表：使用 db.Table 定义多对多关联
# 双向存储：A加B为好友时，插入 (A,B) 和 (B,A) 两条记录
# 查询"我的好友列表"只需 WHERE user_id = 我的ID
friends = db.Table(
    'friends',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户ID'),
    db.Column('friend_id', db.Integer, db.ForeignKey('users.id'), nullable=False, comment='好友ID'),
    db.Column('remark', db.String(64), default=None, comment='备注名'),
    db.Column('created_at', db.DateTime, default=lambda:datetime.now(timezone.utc), comment='添加时间'),
    # 联合唯一约束：防止重复添加好友
    db.UniqueConstraint('user_id', 'friend_id', name='uk_friends')
)


class FriendRequest(db.Model):
    """好友申请表：记录申请→同意/拒绝的完整流程"""
    __tablename__ = 'friend_requests'

    id = db.Column(db.Integer, primary_key=True, comment='申请编号')
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发起人ID')
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='接收人ID')
    status = db.Column(db.SmallInteger, default=0, comment='0=待处理 1=已同意 2=已拒绝')
    message = db.Column(db.String(200), default=None, comment='申请留言')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发起时间')
    handled_at = db.Column(db.DateTime, default=None, comment='处理时间（同意/拒绝的时间）')

    def __repr__(self):
        return f"<FriendRequest {self.sender_id}->{self.receiver_id} status={self.status}>"


class Blacklist(db.Model):
    __tablename__ = 'blacklists'

    id = db.Column(db.Integer, primary_key=True, comment='拉黑名单编号')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='拉黑者')
    blocked_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='被拉黑者')
    reason = db.Column(db.String(200), default=None, comment='拉黑原因')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='拉黑时间')

    def __repr__(self):
        return f"<Blacklist {self.user_id} blocks {self.blocked_id}>"