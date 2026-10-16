from exts import db
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash


class Admin(db.Model):
    """管理端账号：与前台普通用户完全隔离，独立表、独立登录态（session['admin_id']）。"""
    __tablename__ = 'admins'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True, comment='管理员ID')
    username = db.Column(db.String(64), unique=True, nullable=False, comment='登录用户名')
    email = db.Column(db.String(255), default=None, comment='绑定邮箱（可选）')
    nickname = db.Column(db.String(64), default=None, comment='显示名')
    _password = db.Column(db.String(255), default=None, comment='密码哈希')
    role = db.Column(db.SmallInteger, default=2, comment='1=超级管理员 2=普通管理员')
    status = db.Column(db.SmallInteger, default=1, comment='1=正常 0=禁用')
    last_login = db.Column(db.DateTime, default=None, comment='最近登录时间 UTC')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), comment='创建时间')

    def __repr__(self):
        return f"<Admin {self.username}>"

    def __init__(self, *args, **kwargs):
        if "password" in kwargs:
            self.password = kwargs.pop("password")
        super().__init__(*args, **kwargs)

    @property
                                    # @property:把类方法伪装成类属性,只读
    def password(self):
        return self._password

    @password.setter
                                       #  @xxxx.setter:让“看起来像属性赋值”的操作，实际上走你定义的方法。
    def password(self, raw_password):
        self._password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        if not self._password:
            return False
        return check_password_hash(self._password, raw_password)
