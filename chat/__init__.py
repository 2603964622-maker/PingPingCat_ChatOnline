from flask import g, session
from models.user import User
from exts import cache


def ensure_socket_user():
    """
    在 SocketIO 事件处理器中加载当前登录用户。
    SocketIO 事件不走 HTTP 的 before_request 钩子，g.user 不会自动注入，
    因此这里从 session 读取 user_id 并加载用户，缓存到 g.user 上。
    :return: User 对象；未登录/被封禁/已注销返回 None
    """
    user = getattr(g, 'user', None)
    if user is not None:
        return user
    try:
        uid = session.get('user_id')
    except Exception:
        uid = None
    if not uid:
        return None
    # 单设备登录：socket 事件不走 before_request，这里同样比对 login_token，旧设备被挤后禁止收发
    tk = session.get('login_token')
    if not tk or cache.get(f'login_token:{uid}') != tk:
        return None
    user = User.query.filter_by(id=uid, status=1).first()
    if user and user.deleted_at is None:
        g.user = user
        return user
    return None
