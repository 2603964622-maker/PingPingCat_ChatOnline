from flask import session,g,redirect,url_for
from models.user import User

# 全局变量 g
# g 对象是请求级别的
#1.生命周期极短：Flask 的 g 对象不是全局变量，它是请求级别的。每次用户发来请求，Flask 会创建一个独立的 g 对象；请求结束（页面渲染完/接口返回完），
#它就会被自动销毁回收。
#2. 线程安全：即使很多人同时访问，每个人的 g 对象都是隔离的，互不影响。
#3. 数据量极小：setattr(g, 'user', user) 存的是一个数据库用户对象（通常就几十条字段），可能就几 KB 内存。不像存视频/大文件，所以完全不担心内存爆掉。

def before_request():
    if session.get('user_id') is None:
        return redirect(url_for('user.login'))
    user = User.query.filter_by(user_id=session.get('user_id')).first()
    setattr(g,'user',user)
    return None