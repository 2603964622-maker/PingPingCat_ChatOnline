from flask import session,g,redirect,url_for,request
from models.user import User
from exts import cache
from utils import refresh_user_ban

# 全局变量 g
# g 对象是请求级别的
#1.生命周期极短：Flask 的 g 对象不是全局变量，它是请求级别的。每次用户发来请求，Flask 会创建一个独立的 g 对象；请求结束（页面渲染完/接口返回完），
#它就会被自动销毁回收。
#2. 线程安全：即使很多人同时访问，每个人的 g 对象都是隔离的，互不影响。
#3. 数据量极小：setattr(g, 'user', user) 存的是一个数据库用户对象（通常就几十条字段），可能就几 KB 内存。不像存视频/大文件，所以完全不担心内存爆掉。

def _session_token_valid(uid):
    """单设备登录校验：cookie 里的 login_token 必须等于 Redis 中该用户最新一次登录的 token"""
    tk = session.get('login_token')
    return bool(tk) and cache.get(f'login_token:{uid}') == tk


def before_request():
    # 管理端（/admin 前缀）拥有独立的登录态与鉴权（session['admin_id'] + admin_required），
    # 完全绕开前台用户钩子，避免管理员请求被重定向到用户登录页
    if request.path == '/admin' or request.path.startswith('/admin/'):
        return None
    # 缩略图分发：桶本身 public-read，缩略图只是它的派生副本，不额外鉴权——
    # 管理端（独立 admin 登录态）、举报详情等场景才能正常出图；路由内部只认本桶 key，不转外链
    if request.path == '/upload/thumb':
        return None
    # 白名单：静态资源与登录/注册/验证码/改密码接口（免登录可调，否则登录页自身都加载不出样式）
    if request.endpoint in ('static', 'user.login', 'user.register', 'user.register_mail_captcha',
                            'user.update_passwd_mail_captcha', 'user.update_password'):
        # 免登录但非排斥登录态：若已有 session 仍加载 g.user，
        # 让改密码接口能识别“已登录只能改自己”，主界面弹层不传 email 也能拿到本人邮箱
        uid = session.get('user_id')
        if uid is not None:
            u = User.query.filter_by(id=uid).first()
            if u is not None and u.deleted_at is None and u.status == 1 and _session_token_valid(uid):
                setattr(g, 'user', u)
        return None
    if session.get('user_id') is None:
        return redirect(url_for('user.login'))
    user = User.query.filter_by(id=session.get('user_id')).first()
    # 定时封禁到期自动解封（在封禁判定之前）
    refresh_user_ban(user)
    # 账号不存在、已注销(deleted_at) 或 已被封禁(status=0)，清除登录态并强制重新登录
    if user is None or user.deleted_at is not None or user.status != 1:
        session.clear()
        return redirect(url_for('user.login'))
    # 单设备登录：同账号在别处重新登录后，旧设备的 token 已失效，踢回登录页（kick=1 供登录页提示原因）
    if not _session_token_valid(user.id):
        session.clear()
        return redirect(url_for('user.login', kick=1))
    setattr(g,'user',user)
    return None