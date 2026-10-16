from exts import db
from datetime import datetime,timezone
"""
    BotTemplate-ORM:Bot模板表
    GroupBot-ORM:群聊Bot关联表
    ChannelBot-ORM:频道Bot关联表
    BotLog-ORM:Bot日志表
"""

class BotTemplate(db.Model):
    __tablename__ = 'bot_templates'

    id = db.Column(db.Integer, primary_key=True, comment='模板ID')
    code = db.Column(db.String(50), unique=True, default=None, comment='程序唯一标识(引擎按此派发规则，永不修改)')
    name = db.Column(db.String(64), nullable=False, comment='Bot名称(展示用，可改)')
    description = db.Column(db.String(200), default=None, comment='功能描述')
    avatar = db.Column(db.String(500), default=None, comment='Bot头像')
    config = db.Column(db.Text, default=None, comment='JSON默认配置(各群未覆盖时生效)')
    status = db.Column(db.SmallInteger, default=1, comment='1=启用 0=停用')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='创建时间')

    def __repr__(self):
        return f"<BotTemplate {self.name}>"


class GroupBot(db.Model):
    __tablename__ = 'group_bots'

    id = db.Column(db.Integer, primary_key=True, comment='群Bot绑定ID')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=False, comment='群ID')
    bot_template_id = db.Column(db.Integer, db.ForeignKey('bot_templates.id'), nullable=False, comment='模板ID')
    enabled = db.Column(db.SmallInteger, default=1, comment='1=开启 0=关闭')
    config = db.Column(db.Text, default=None, comment='群级JSON配置，覆盖模板默认')
    added_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='添加时间')

    __table_args__ = (
        db.UniqueConstraint('group_id', 'bot_template_id', name='uk_group_bot'),
    )

    def __repr__(self):
        return f"<GroupBot g={self.group_id} b={self.bot_template_id}>"


class ChannelBot(db.Model):
    __tablename__ = 'channel_bots'

    id = db.Column(db.Integer, primary_key=True, comment='频道Bot绑定ID')
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False, comment='频道ID')
    bot_template_id = db.Column(db.Integer, db.ForeignKey('bot_templates.id'), nullable=False, comment='模板ID')
    enabled = db.Column(db.SmallInteger, default=1, comment='1=开启 0=关闭')
    config = db.Column(db.Text, default=None, comment='频道级JSON配置，覆盖模板默认')
    added_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='添加时间')

    __table_args__ = (
        db.UniqueConstraint('channel_id', 'bot_template_id', name='uk_channel_bot'),
    )

    def __repr__(self):
        return f"<ChannelBot c={self.channel_id} b={self.bot_template_id}>"


class BotLog(db.Model):
    __tablename__ = 'bot_logs'

    id = db.Column(db.Integer, primary_key=True, comment='日志ID')
    bot_template_id = db.Column(db.Integer, db.ForeignKey('bot_templates.id'), nullable=False, comment='Bot模板ID')
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), default=None, comment='关联群ID')
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), default=None, comment='关联频道ID')
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), default=None, comment='目标用户ID')
    action = db.Column(db.String(50), nullable=False, comment='执行动作(kick/mute/delete_msg/warning)')
    reason = db.Column(db.String(200), default=None, comment='动作原因')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='执行时间')

    def __repr__(self):
        return f"<BotLog action={self.action} target={self.target_user_id}>"