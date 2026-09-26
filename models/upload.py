from exts import db
from datetime import datetime,timezone
"""
    UploadFile-ORM:文件上传表
    ConversationFile-ORM:用户会话-文件关联表
    GroupFile-ORM:群聊-文件关联表
    ChannelFile-ORM:频道-文件关联表
"""

class AvatarFile(db.Model):
    __tablename__ = 'avatar_files'

    id = db.Column(db.Integer, primary_key=True, comment='文件ID',autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='上传者')
    filename = db.Column(db.String(255), nullable=False, comment='原始文件名')
    file_url = db.Column(db.String(500), nullable=False, comment='存储路径/URL')
    file_type = db.Column(db.SmallInteger, default=1, comment='1=头像 2=视频 3=图片 4=其他')
    file_size = db.Column(db.Integer, default=0, comment='文件大小(字节)')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='上传时间')

    def __repr__(self):
        return f"<UploadFile {self.filename}>"


class ConversationFile(db.Model):
    __tablename__ = 'conversation_files'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='关联ID')
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False, comment='会话ID')
    file_id = db.Column(db.Integer, db.ForeignKey('upload_files.id'), nullable=False, comment='文件ID')
    file_type = db.Column(db.SmallInteger, comment='1=头像 2=视频 3=图片 4=其他',nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')

    def __repr__(self):
        return f"<ConversationFile conv={self.conversation_id} file={self.file_id}>"


class GroupFile(db.Model):
    __tablename__ = 'group_files'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='关联ID')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群聊ID')
    file_url = db.Column(db.String(100), nullable=False, comment='文件url')
    filename = db.Column(db.String(100),nullable=False,comment="文件名称")
    file_type = db.Column(db.SmallInteger, comment='1=头像 2=视频 3=图片 4=其他')
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')
    group = db.relationship('Group', back_populates='group_files')

    def __repr__(self):
        return f"<GroupFile group={self.group_id} file={self.file_id}>"


class ChannelFile(db.Model):
    __tablename__ = 'channel_files'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='关联ID')
    post_id = db.Column(db.Integer, db.ForeignKey('channel_posts.id'), nullable=True, comment='帖子ID')
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False, comment='频道ID')
    file_id = db.Column(db.Integer, db.ForeignKey('upload_files.id'), nullable=False, comment='文件ID')
    file_url = db.Column(db.String(100), nullable=False, comment='存储路径/URL')
    file_type = db.Column(db.SmallInteger, comment='1=头像 2=视频 3=图片 4=其他')
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='发送者ID')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='发送时间')
    #关系
    posts = db.relationship('ChannelPost', back_populates='channel_files')
    channel = db.relationship('Channel', back_populates='channel_files')
    def __repr__(self):
        return f"<ChannelFile channel={self.channel_id} file={self.file_id}>"
