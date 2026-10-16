from exts import db
from datetime import datetime,timezone

class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True, comment='消息ID',autoincrement=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='接收者ID')
    content = db.Column(db.Text, nullable=False, comment='消息内容')
    msg_type = db.Column(db.SmallInteger, default=1, comment='1=文本 2=图片 3=文件')
    reply_to = db.Column(db.Integer, default=None, comment='引用回复的消息ID(指向本会话内另一条 messages.id)，空=非回复')
    read_status = db.Column(db.SmallInteger, default=0, comment='0=未读 1=已读')
    deleted_at = db.Column(db.DateTime, default=None, comment='软删除标记')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')

    sender = db.relationship('User', back_populates='sent_messages', foreign_keys=[sender_id])

    def __repr__(self):
        return f"<Message {self.id} from={self.sender_id} to={self.receiver_id}>"


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True, comment='会话ID')
    user1_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户1')
    user2_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户2')
    last_msg_id = db.Column(db.Integer, default=None, comment='最后一条消息ID')
    updated_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), comment='更新时间')

    def __repr__(self):
        return f"<Conversation {self.user1_id}-{self.user2_id}>"


class MessageDelete(db.Model):
    """私聊消息按用户软删除表：一方删除不影响另一方（message_id + user_id 唯一）"""
    __tablename__ = 'message_deletes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='记录ID')
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=False, comment='消息ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='执行删除的用户')
    deleted_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='删除时间')

    __table_args__ = (
        db.UniqueConstraint('message_id', 'user_id', name='uk_message_delete'),
    )

    def __repr__(self):
        return f"<MessageDelete m={self.message_id} u={self.user_id}>"


class UserConversation(db.Model):
    """统一会话列表：私聊/群聊/频道共用，收到消息即刷新 updated_at 置顶"""
    __tablename__ = 'user_conversations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='会话ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='会话归属用户')
    conv_type = db.Column(db.String(10), nullable=False, comment='private/group/channel')
    target_id = db.Column(db.Integer, nullable=False, comment='私聊=对方用户id 群=group_id 频道=channel_id')
    last_msg_id = db.Column(db.Integer, default=None, comment='最后一条消息/帖子id')
    last_content = db.Column(db.String(255), default=None, comment='最后内容摘要')
    unread_count = db.Column(db.Integer, default=0, nullable=False, comment='未读数')
    notify_level = db.Column(db.SmallInteger, default=1, server_default='1', nullable=False, comment='通知级别 1=正常 3=静音（私聊静音：来音静默、角标退化为灰点）')
    updated_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), onupdate=lambda:datetime.now(timezone.utc), comment='最近活跃时间')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'conv_type', 'target_id', name='uk_user_conversation'),
        db.Index('idx_user_conv_updated', 'user_id', 'updated_at'),
    )

    def __repr__(self):
        return f"<UserConversation u={self.user_id} {self.conv_type}:{self.target_id}>"