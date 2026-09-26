from functools import wraps
from typing import Any, Callable

from flask import redirect,url_for,g,jsonify,request
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
#----------------------------------------------------------
