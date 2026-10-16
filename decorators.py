from functools import wraps
from typing import Any, Callable

from flask import redirect,url_for,g,jsonify,request,session
from werkzeug import Response
#---------------------------------------------------------
def login_required(func):
    """
    登录验证器，对于需要登录的功能进行验证
    :param func: user.login
    :return: 满足条件后返回原函数的元数据
    """
    @wraps(func)
    def wrapper(*args,**kwargs):
        if not hasattr(g,'user'):
            return redirect(url_for('user.login'))
        else:
            return func(*args,**kwargs)
    return wrapper


def admin_required(func):
    """管理端鉴权：仅依赖独立会话 session['admin_id']（由 /admin/login 写入），
    与前台用户完全隔离。未登录统一返回 JSON 401（管理端页面/接口均为前端 fetch 驱动）。"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if session.get('admin_id') is None:
            if request.accept_mimetypes.best == 'application/json' or request.path.startswith('/admin/api'):
                return jsonify({"code": 401, "msg": "未登录或登录已失效", "data": None}), 401
            return redirect(url_for('admin.login'))
        return func(*args, **kwargs)
    return wrapper
#----------------------------------------------------------
