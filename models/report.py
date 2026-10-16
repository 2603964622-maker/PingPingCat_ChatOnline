from exts import db
from datetime import datetime, timezone


class Report(db.Model):
    """举报表：前台用户对违规的「用户 / 群聊 / 频道」发起举报，附文字理由（必填）与可选截图。
    管理端在 /admin 审核并处置（驳回 / 通过并对被举报对象强制解散 + 封禁责任人）。"""
    __tablename__ = 'reports'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='举报ID')
    reporter_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='举报人用户ID')
    target_type = db.Column(db.String(16), nullable=False, comment='举报对象类型 user/group/channel')
    target_id = db.Column(db.Integer, nullable=False, comment='被举报对象ID（用户uid/群id/频道id）')
    target_name = db.Column(db.String(128), default=None, comment='举报时快照的对象名（便于列表展示）')
    reason = db.Column(db.Text, nullable=False, comment='举报理由（必填）')
    images = db.Column(db.Text, default=None, comment='举报截图URL列表，JSON数组字符串（可选）')
    status = db.Column(db.SmallInteger, default=0, comment='0=待处理 1=已处理(处置) 2=已驳回')
    handle_note = db.Column(db.String(500), default=None, comment='管理员处理备注')
    handled_by = db.Column(db.Integer, db.ForeignKey('admins.id'), default=None, comment='处理人管理员ID')
    handled_at = db.Column(db.DateTime, default=None, comment='处理时间 UTC')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), comment='举报时间')

    __table_args__ = (
        db.Index('ix_report_target', 'target_type', 'target_id', 'status'),
        db.Index('ix_report_reporter', 'reporter_id', 'created_at'),
    )

    def __repr__(self):
        return f"<Report {self.target_type}#{self.target_id} by={self.reporter_id}>"


class FileLog(db.Model):
    """文件流水表：集中记录每一次文件上传 / 删除事件，供管理端统计「上传/删除比率」等趋势。
    在 utils.save_upload_file / delete_old_file 两个集中入口埋点写入。"""
    __tablename__ = 'file_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='流水ID')
    action = db.Column(db.String(10), nullable=False, comment='upload=上传 delete=删除')
    file_url = db.Column(db.String(500), default=None, comment='文件URL/路径')
    file_type = db.Column(db.SmallInteger, default=None, comment='1=头像 2=视频 3=图片 4=其他')
    file_size = db.Column(db.BigInteger, default=None, comment='文件字节大小（上传时记录），供管理端汇总上传总量')
    user_id = db.Column(db.Integer, default=None, comment='操作用户ID（可空，尽力记录）')
    scene = db.Column(db.String(24), default=None, comment='来源场景（可选，如 avatar/group/channel/moment）')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True, comment='发生时间')

    def __repr__(self):
        return f"<FileLog {self.action} {self.file_url}>"
