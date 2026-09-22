from exts import db
from datetime import datetime,timezone

class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True, comment='消息ID')
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='接收者ID')
    content = db.Column(db.Text, nullable=False, comment='消息内容')
    msg_type = db.Column(db.SmallInteger, default=1, comment='1=文本 2=图片 3=文件')
    read_status = db.Column(db.SmallInteger, default=0, comment='0=未读 1=已读')
    deleted_at = db.Column(db.DateTime, default=None, comment='软删除标记')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')

    def __repr__(self):
        return f"<Message {self.id} from={self.sender_id} to={self.receiver_id}>"


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id = db.Column(db.Integer, primary_key=True, comment='会话ID')
    user1_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户1')
    user2_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='用户2')
    last_msg_id = db.Column(db.Integer, default=None, comment='最后一条消息ID')
    updated_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), onupdate=datetime.utcnow, comment='更新时间')

    def __repr__(self):
        return f"<Conversation {self.user1_id}-{self.user2_id}>"