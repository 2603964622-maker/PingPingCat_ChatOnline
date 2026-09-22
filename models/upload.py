from exts import db
from datetime import datetime,timezone
"""
    UploadFile-ORM:文件上传表
"""

class UploadFile(db.Model):
    __tablename__ = 'upload_files'

    id = db.Column(db.Integer, primary_key=True, comment='文件ID')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, comment='上传者')
    filename = db.Column(db.String(255), nullable=False, comment='原始文件名')
    file_url = db.Column(db.String(500), nullable=False, comment='存储路径/URL')
    file_type = db.Column(db.SmallInteger, default=1, comment='1=图片 2=视频 3=音频 4=文档 5=压缩包')
    file_size = db.Column(db.Integer, default=0, comment='文件大小(字节)')
    created_at = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc), comment='上传时间')

    def __repr__(self):
        return f"<UploadFile {self.filename}>"
