from functools import wraps
from typing import Any, Callable

from flask import redirect,url_for,g,jsonify,request
from werkzeug import Response


def login_required(func):
    @wraps(func)
    def wrapper(*args,**kwargs):
        if not hasattr(g,'user'):
            return redirect(url_for('user.login'))
        else:
            return func(*args,**kwargs)
    return wrapper