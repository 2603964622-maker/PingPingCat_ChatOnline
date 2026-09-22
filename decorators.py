from functools import wraps
from flask import redirect,url_for,g,jsonify,request

def login_required(func):
    @wraps(func)
    def wrapper(*args,**kwargs):
        if not hasattr(g,'user'):
            return redirect(url_for('user.login'))
        else:
            return func(*args,**kwargs)
    return wrapper