import json
from datetime import datetime, timezone, timedelta, time as _time

from flask import Blueprint, request, session, render_template, current_app, g

from decorators import admin_required
from exts import db, socketio
from models.admin import Admin
from models.report import Report, FileLog
from models.feedback import Feedback
from models.announcement import SiteAnnouncement, SiteAnnouncementRead
from models.user import User, UserOnlineLog
from presence import is_online as _presence_online, online_users as _presence_users
from models.group import Group, GroupMember, GroupMessage, GroupAnnouncement, AnnouncementUserIsRead
from models.channel import Channel, ChannelMember, ChannelPost, ChannelComment, ChannelLike, ChannelAnnouncement
from models.upload import GroupFile, ChannelFile
from models.message import UserConversation
from utils import (resp_json, OK, PARAMS_ERROR, SERVER_ERROR, create_notification,
                   save_upload_file, get_file_size, delete_old_file)

bp = Blueprint('admin', __name__, url_prefix='/admin')

# 举报对象类型→中文（用于通知文案）
_TARGET_KIND = {'user': '用户', 'group': '群聊', 'channel': '频道'}


def _target_desc(r):
    """举报对象的可读描述，如「群聊 xxx」。"""
    kind = _TARGET_KIND.get(r.target_type, r.target_type)
    return '%s「%s」' % (kind, r.target_name or ('#' + str(r.target_id)))


def _current_admin():
    aid = session.get('admin_id')
    return Admin.query.get(aid) if aid else None  # 如果aid存在就在Admin查找一下，否则返回None
    # Python三元表达式:干活的（返回值） if 条件 else 兜底值
    # ORM-Class.query.get():按主键查一条，返回模型对象，找不到返回 `None`



# ============================ 登录 / 注册 / 登出 ============================
@bp.route('/register', methods=['GET', 'POST'])
def register():
    """管理端注册（与前台用户注册完全分开）。GET 渲染页面，POST 建号。
    若配置了 ADMIN_SIGNUP_KEY（环境变量），注册必须携带匹配的邀请密钥。"""
    if request.method == 'GET':
        return render_template('admin/register.html')
    data = request.get_json(silent=True) or {}   # 不论是否正确解析 前端传来的 JSON 数据，这里最终都确保返回一个空字典
    # request.get_json()括号中默认不写是silent = False,解析失败时抛出异常;silent=True: 解释失败不报错，返回None
    # None 在布尔上下文中永远被当作假值（falsy）
    # 布尔上下文 = Python 需要判断“是真是假”的地方，包括 if、while、or、and、not、条件表达式。

    # A or B 运算规则 :
    # A 为真 → 结果取 A 的原值，并且 B 根本不会被计算/执行（短路）
    # A 为假 → 结果取 B 的原值，不管 B 是真还是假，拿到就直接用，不会再兜底

    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    nickname = (data.get('nickname') or '').strip() or username
    invite = (data.get('invite_key') or '').strip()

    required_key = current_app.config.get('ADMIN_SIGNUP_KEY')
    #从当前 Flask 应用实例的配置对象 config 中，通过键名获取对应的配置值
    if required_key and invite != required_key:
        # A and B:表达式取值时【这个知识点和代码无关联】
        # A 为真 → 结果是 B 的原值（不管 B 真假）
        # A 为假 → 结果是 A 本身，B 不执行（短路
        return resp_json(PARAMS_ERROR, "邀请密钥缺失或错误", data=None)
    if not username or len(username) < 3:
        return resp_json(PARAMS_ERROR, "用户名至少 3 个字符", data=None)
    if len(password) < 6:
        return resp_json(PARAMS_ERROR, "密码至少 6 位", data=None)
    if Admin.query.filter_by(username=username).first(): #  ORM-Class.query.filter_by(字段=值, ...):等值条件过滤，关键字参数写法，返回查询对象
        return resp_json(PARAMS_ERROR, "该用户名已存在", data=None)

    # 首个注册的管理员自动成为超级管理员
    is_first = Admin.query.count() == 0   # ORM-Class.query.count()：统计符合条件记录数，返回整数（不返回列表）
    admin = Admin(username=username, password=password, nickname=nickname,
                  role=1 if is_first else 2)
    db.session.add(admin)
    db.session.commit()
    return resp_json(OK, "注册成功，请登录", data={"username": username})


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('admin/login.html')
    data = request.get_json(silent=True) or {}  # 如果没有成功解析JSON的话，不报错，返回None,然后变成空字典
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return resp_json(PARAMS_ERROR, "用户名和密码不能为空", data=None)
    admin = Admin.query.filter_by(username=username).first()
    # 为什么要使用username查询?
    #管理员在登录页填的是用户名 + 密码，此时后端根本不知道这个人的 id 是多少——id 是注册时数据库自增生成的，登录者不需要也不可能记得它。
    # 我们已经给username 设置了 唯一索引，不用担心重复的问题
    if admin is None or not admin.check_password(password):
        return resp_json(PARAMS_ERROR, "用户名或密码错误", data=None)
    if admin.status != 1:
        return resp_json(403, "该管理员账号已被禁用", data=None)
    admin.last_login = datetime.now(timezone.utc)   # datetime.now()来自“from datetime import datetime”,用来获取当前的时间 有时区版本
    db.session.commit()  # 对于查询出来的对象，只需要调用commit()即可，不需要在执行db.session.add()了,只有新建ORM对象的时候才会写
    session['admin_id'] = admin.id
    session.permanent = True  #设置session过期时间,不写这个的话默认关闭浏览器，下次还需要直接登录
    # 如果不配置 PERMANENT_SESSION_LIFETIME 的话,默认就是31天过期，
    # 只要 permanent = True，Flask 就会去读 PERMANENT_SESSION_LIFETIME 来决定 cookie 的 Expires。
    return resp_json(OK, "登录成功", data={
        "id": admin.id, "username": admin.username, "nickname": admin.nickname, "role": admin.role,
    })


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    # 清除session的两种方式:
    # ① session.pop()【只清理自身的登录态，保留其他session数据】
    # ② session.clear()【彻底清空所有session数据】
    session.pop('admin_id', None) # 退出本质就是清除session
    return resp_json(OK, "已退出登录", data=None)


@bp.route('/dashboard')
@admin_required
def dashboard():
    return render_template('admin/dashboard.html', admin=_current_admin())
    # 它查出的是一条完整的 Admin 记录（含 id、username、role、status、created_at 等所有字段），作为模板变量传给 Jinja2 做服务端渲染。



# ============================ 统计看板 ============================
def _daily_counts(model, date_col, days, extra_filters=()):
    """按天聚合最近 days 天（含今天）的新增数量，返回 (labels[], counts[])。created_at 视为 UTC naive。"""
    from sqlalchemy import func
    # func 就是 SQLAlchemy 里用来在 Python 代码中调用数据库侧 SQL 函数（如 COUNT、SUM、NOW 等）的工具，让统计和聚合逻辑在数据库里算，而不是拉到 Python 里算。
    today = datetime.now(timezone.utc).date() # 不加后面的date(),就是时间戳"2023-04-05 12:34:56.789012+00:00"
    start = today - timedelta(days=days - 1)
    q = db.session.query(func.date(date_col).label('d'), func.count().label('c')) \
        .filter(date_col >= datetime.combine(start, _time.min))
    for f in extra_filters:
        q = q.filter(f)
    rows = q.group_by('d').all()
    m = {str(d): int(c) for d, c in rows}
    labels, counts = [], []
    for i in range(days):
        day = start + timedelta(days=i)
        labels.append(day.isoformat())
        counts.append(m.get(day.isoformat(), 0))
    return labels, counts


def _filelog_counts(action, days):
    """FileLog 按天统计上传/删除事件数。"""
    from sqlalchemy import func
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    rows = db.session.query(func.date(FileLog.created_at).label('d'), func.count().label('c')) \
        .filter(FileLog.action == action, FileLog.created_at >= datetime.combine(start, _time.min)) \
        .group_by('d').all()
    m = {str(d): int(c) for d, c in rows}
    labels, counts = [], []
    for i in range(days):
        day = start + timedelta(days=i)
        labels.append(day.isoformat())
        counts.append(m.get(day.isoformat(), 0))
    return labels, counts


def _as_utc(dt):
    """MySQL DATETIME 读回为 naive UTC，统一补上 tzinfo 以便比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _online_stats(days):
    """统计最近 days 天（含今天）的用户在线时长（基于 user_online_logs 会话流水）。
    - 已关闭会话：直接取 duration_seconds
    - 未关闭会话（logout_at=NULL）：仅当用户当前在线才按 已在线时长 计入，
      避免服务异常重启遗留的孤儿记录被错算
    - 单条封顶 24h，防脏数据拉高均值
    返回 {avg_seconds, total_seconds, users, online_now}。"""
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    start_dt = datetime.combine(start, _time.min)
    now = datetime.now(timezone.utc)
    logs = UserOnlineLog.query.filter(UserOnlineLog.login_at >= start_dt).all()
    per_user = {}
    for lg in logs:
        if lg.logout_at is not None:
            secs = lg.duration_seconds
            if secs is None:  # 历史数据兼容：未存时长时现算
                secs = int((_as_utc(lg.logout_at) - _as_utc(lg.login_at)).total_seconds())
        elif _presence_online(lg.user_id):
            secs = int((now - _as_utc(lg.login_at)).total_seconds())
        else:
            continue  # 孤儿记录，忽略
        secs = max(0, min(secs, 24 * 3600))
        per_user[lg.user_id] = per_user.get(lg.user_id, 0) + secs
    total = sum(per_user.values())
    return {
        "avg_seconds": int(total / len(per_user)) if per_user else 0,
        "total_seconds": int(total),
        "users": len(per_user),
        # 实时在线直接数连接注册表，不用“登录流水 ∩ 当前在线”：
        # 后者会因流水未写入而漏计，导致卡片数字与 /api/online_users 的名单对不上
        "online_now": len(_presence_users()),
    }


def _growth_delta(model, date_col, days, extra_filters=()):
    """当前区间新增 vs 上一个等长区间新增（环比）。返回 {cur, prev, pct}。"""
    from sqlalchemy import func
    today = datetime.now(timezone.utc).date()
    cur_start = today - timedelta(days=days - 1)
    prev_start = cur_start - timedelta(days=days)

    def _count(d0, d1):
        q = db.session.query(func.count()).select_from(model).filter(
            date_col >= datetime.combine(d0, _time.min),
            date_col < datetime.combine(d1, _time.min))
        for f in extra_filters:
            q = q.filter(f)
        return int(q.scalar() or 0)

    cur = _count(cur_start, today + timedelta(days=1))
    prev = _count(prev_start, cur_start)
    if prev:
        pct = round((cur - prev) / prev * 100, 1)
    else:
        pct = 100.0 if cur else 0.0
    return {"cur": cur, "prev": prev, "pct": pct}


def _upload_size(days):
    """最近 days 天上传文件总字节数（action=upload 的 FileLog.file_size 求和）。"""
    from sqlalchemy import func
    start = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    total = db.session.query(func.coalesce(func.sum(FileLog.file_size), 0)).filter(
        FileLog.action == 'upload',
        FileLog.created_at >= datetime.combine(start, _time.min)).scalar()
    return int(total or 0)


@bp.route('/api/stats', methods=['GET'])
@admin_required
def api_stats():
    try:
        days = min(max(int(request.args.get('days', 7) or 7), 1), 90)
        totals = {
            "users": User.query.filter(User.deleted_at.is_(None)).count(),
            "groups": Group.query.filter_by(status=1).count(),
            "channels": Channel.query.filter_by(status=1).count(),
            "posts": ChannelPost.query.filter_by(status=1).count(),
            "pending_reports": Report.query.filter_by(status=0).count(),
            "total_reports": Report.query.count(),
            "pending_feedbacks": Feedback.query.filter_by(status=0).count(),
            "total_feedbacks": Feedback.query.count(),
            "published_announcements": SiteAnnouncement.query.filter_by(status=1).count(),
        }
        u_labels, u_counts = _daily_counts(User, User.created_at, days, (User.deleted_at.is_(None),))
        g_labels, g_counts = _daily_counts(Group, Group.created_at, days)
        c_labels, c_counts = _daily_counts(Channel, Channel.created_at, days)
        p_labels, p_counts = _daily_counts(ChannelPost, ChannelPost.created_at, days)
        f_labels, up_counts = _filelog_counts('upload', days)
        _, del_counts = _filelog_counts('delete', days)
        online = _online_stats(days)
        delta = {
            "users": _growth_delta(User, User.created_at, days, (User.deleted_at.is_(None),)),
            "groups": _growth_delta(Group, Group.created_at, days),
            "channels": _growth_delta(Channel, Channel.created_at, days),
            "posts": _growth_delta(ChannelPost, ChannelPost.created_at, days),
        }
        return resp_json(OK, "ok", data={
            "days": days,
            "totals": totals,
            "online": online,
            "growth": {
                "labels": u_labels,
                "users": u_counts,
                "groups": g_counts,
                "channels": c_counts,
                "posts": p_counts,
                "delta": delta,
            },
            "files": {
                "labels": f_labels,
                "upload": up_counts,
                "delete": del_counts,
                "upload_total": sum(up_counts),
                "delete_total": sum(del_counts),
                "size_bytes": _upload_size(days),
            },
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"统计失败：{str(e)}", data=None)


@bp.route('/api/online_users', methods=['GET'])
@admin_required
def api_online_users():
    """实时在线名单：直接读 SocketIO 进程内连接注册表（presence.online_users），
    与看板“实时在线”卡片同源，不会出现数字与列表不一致。
    本次上线时间取该用户最近一条未关闭的在线流水（与 disconnect 里的取法一致）。"""
    try:
        conns = _presence_users()
        if not conns:
            return resp_json(OK, "ok", data={"total": 0, "connections": 0, "users": []})
        uids = list(conns.keys())
        users = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()}
        # 服务异常重启可能遗留多条 logout_at=NULL 的孤儿记录，按 id 升序覆盖后拿到的是最新一条
        login_map = {}
        for lg in UserOnlineLog.query.filter(
            UserOnlineLog.user_id.in_(uids), UserOnlineLog.logout_at.is_(None)
        ).order_by(UserOnlineLog.id.asc()).all():
            login_map[lg.user_id] = lg.login_at
        now = datetime.now(timezone.utc)
        items = []
        for uid, n in conns.items():
            u = users.get(uid)
            login_at = _as_utc(login_map.get(uid))
            items.append({
                "id": uid,
                "number": u.number if u else None,
                "nickname": u.nickname if u else "（用户记录缺失）",
                "avatar": u.avatar if u else None,
                "status": u.status if u is not None else None,
                "deleted": bool(u.deleted_at) if u else False,
                "connections": n,          # >1 表示同一账号多端/多标签页在线
                "login_at": login_at.strftime('%Y-%m-%d %H:%M:%S') if login_at else None,
                "duration_seconds": max(0, int((now - login_at).total_seconds())) if login_at else None,
            })
        # 刚上线的排前面，便于看“谁刚进来”；没有流水记录的排末尾
        items.sort(key=lambda x: x["login_at"] or "", reverse=True)
        return resp_json(OK, "ok", data={
            "total": len(items),
            "connections": int(sum(conns.values())),
            "users": items,
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"获取在线名单失败：{str(e)}", data=None)


# ============================ 举报审核 ============================
def _report_dict(r, with_reporter=True):
    reporter = None
    if with_reporter:
        u = User.query.get(r.reporter_id)
        reporter = {"id": r.reporter_id, "nickname": u.nickname if u else None,
                    "number": u.number if u else None} if u else {"id": r.reporter_id}
    imgs = []
    if r.images:
        try:
            imgs = json.loads(r.images)
        except Exception:
            imgs = []
    # 被举报对象为“用户”时附其用户编号，前端按编号展示、不显昵称
    target_number = None
    if r.target_type == 'user':
        tu = User.query.get(r.target_id)
        target_number = tu.number if tu else None
    return {
        "id": r.id, "target_type": r.target_type, "target_id": r.target_id,
        "target_name": r.target_name, "target_number": target_number,
        "reason": r.reason, "images": imgs,
        "status": r.status, "handle_note": r.handle_note,
        "reporter": reporter,
        "created_at": r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else None,
        "handled_at": r.handled_at.strftime('%Y-%m-%d %H:%M:%S') if r.handled_at else None,
    }


@bp.route('/api/reports', methods=['GET'])
@admin_required
def api_reports():
    try:
        status = request.args.get('status')
        limit = min(int(request.args.get('limit', 30) or 30), 100)
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        q = Report.query
        if status in ('0', '1', '2'):
            q = q.filter(Report.status == int(status))
        total = q.count()
        rows = q.order_by(Report.status.asc(), Report.id.desc()).offset(offset).limit(limit).all()
        return resp_json(OK, "ok", data={
            "total": total,
            "reports": [_report_dict(r) for r in rows],
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"获取举报失败：{str(e)}", data=None)


@bp.route('/api/report/detail', methods=['POST'])
@admin_required
def api_report_detail():
    data = request.get_json(silent=True) or {}
    rid = data.get('report_id')
    r = Report.query.get(rid) if rid else None
    if not r:
        return resp_json(PARAMS_ERROR, "举报不存在", data=None)
    d = _report_dict(r)
    # 附带被举报对象的责任人（群主/频道主）信息，供处置参考
    d["owner"] = _target_owner(r.target_type, r.target_id)
    return resp_json(OK, "ok", data=d)


def _target_owner(target_type, target_id):
    """返回被举报对象的责任人：群/频道取群主/频道主，用户取自身。"""
    try:
        if target_type == 'group':
            grp = Group.query.get(target_id)
            if grp and grp.owner_id:
                u = User.query.get(grp.owner_id)
                return {"user_id": grp.owner_id, "nickname": u.nickname if u else None,
                        "name": grp.name, "exists": grp.status == 1}
        elif target_type == 'channel':
            ch = Channel.query.get(target_id)
            if ch and ch.owner_id:
                u = User.query.get(ch.owner_id)
                return {"user_id": ch.owner_id, "nickname": u.nickname if u else None,
                        "name": ch.name, "exists": ch.status == 1}
        elif target_type == 'user':
            u = User.query.get(target_id)
            if u:
                return {"user_id": u.id, "nickname": u.nickname, "name": u.nickname,
                        "exists": u.deleted_at is None}
    except Exception:
        return None
    return None


@bp.route('/api/report/reject', methods=['POST'])
@admin_required
def api_report_reject():
    """驳回举报：{report_id, note}"""
    data = request.get_json(silent=True) or {}
    r = Report.query.get(data.get('report_id'))
    if not r:
        return resp_json(PARAMS_ERROR, "举报不存在", data=None)
    r.status = 2
    r.handle_note = (data.get('note') or '')[:500] or None
    r.handled_by = session.get('admin_id')
    r.handled_at = datetime.now(timezone.utc)
    # 通知举报人：举报经审核未予处理
    create_notification(
        r.reporter_id, 'system', title='举报未通过',
        content='你举报的%s经审核未予处理%s' % (_target_desc(r), ('：' + r.handle_note) if r.handle_note else ''),
        related_id=r.id, related_type='report')
    db.session.commit()
    return resp_json(OK, "已驳回", data=_report_dict(r, with_reporter=False))


# ---------- 强制解散 / 封禁 底层能力（复用业务级联清理逻辑，绕过归属校验） ----------
def _force_disband_group(group_id):
    GroupAnnouncement.query.filter_by(group_id=group_id).delete()
    AnnouncementUserIsRead.query.filter_by(group_id=group_id).delete()
    GroupMessage.query.filter_by(group_id=group_id).delete()
    GroupFile.query.filter_by(group_id=group_id).delete()
    GroupMember.query.filter_by(group_id=group_id).delete()
    UserConversation.query.filter_by(conv_type='group', target_id=group_id).delete()
    grp = Group.query.get(group_id)
    if grp:
        grp.status = 0
        db.session.delete(grp)


def _force_disband_channel(channel_id):
    from models.channel import ChannelInvite
    post_ids = [p.id for p in ChannelPost.query.filter_by(channel_id=channel_id).all()]
    ChannelFile.query.filter_by(channel_id=channel_id).delete()
    if post_ids:
        ChannelLike.query.filter(ChannelLike.post_id.in_(post_ids)).delete(synchronize_session=False)
    ChannelComment.query.filter_by(channel_id=channel_id).delete()
    ChannelAnnouncement.query.filter_by(channel_id=channel_id).delete()
    ChannelPost.query.filter_by(channel_id=channel_id).delete()
    ChannelMember.query.filter_by(channel_id=channel_id).delete()
    try:
        ChannelInvite.query.filter_by(channel_id=channel_id).delete()
    except Exception:
        pass
    UserConversation.query.filter_by(conv_type='channel', target_id=channel_id).delete()
    ch = Channel.query.get(channel_id)
    if ch:
        ch.status = 0
        db.session.delete(ch)


def _ban_user(user_id, days):
    """封禁用户。days<=0 表示永久。返回 (ok, msg)。"""
    u = User.query.get(user_id)
    if not u or u.deleted_at is not None:
        return False, "用户不存在"
    if getattr(u, 'role', None) == 1:
        pass  # 预留：不允许封禁超级用户（当前无此字段，忽略）
    u.status = 0
    if days and days > 0:
        u.banned_until = datetime.now(timezone.utc) + timedelta(days=days)
    else:
        u.banned_until = None
    db.session.commit()
    return True, ("已封禁 %d 天" % days) if days and days > 0 else "已永久封禁"


@bp.route('/api/report/dispose', methods=['POST'])
@admin_required
def api_report_dispose():
    """处置举报：{report_id, disband:0/1, ban_owner:0/1, ban_days:int(0=永久), note}
    - 群聊/频道：disband=强制解散，ban_owner=封禁群主/频道主
    - 用户：ban_owner=封禁该用户本人
    处置成功后举报置为已处理(status=1)。"""
    data = request.get_json(silent=True) or {}
    r = Report.query.get(data.get('report_id'))
    if not r:
        return resp_json(PARAMS_ERROR, "举报不存在", data=None)
    disband = bool(data.get('disband'))
    ban_owner = bool(data.get('ban_owner'))
    try:
        ban_days = int(data.get('ban_days', 0) or 0)
    except (TypeError, ValueError):
        ban_days = 0
    actions = []
    try:
        owner = _target_owner(r.target_type, r.target_id)
        disposed_uid = None   # 被处置人（被举报用户 / 群主 / 频道主）
        ban_msg = None        # 封禁结果文案
        if disband and r.target_type == 'group':
            _force_disband_group(r.target_id)
            actions.append("解散群聊")
            disposed_uid = owner.get("user_id") if owner else None
        elif disband and r.target_type == 'channel':
            _force_disband_channel(r.target_id)
            actions.append("解散频道")
            disposed_uid = owner.get("user_id") if owner else None
        if ban_owner:
            target_uid = None
            if r.target_type == 'user':
                target_uid = r.target_id
            elif owner:
                target_uid = owner.get("user_id")
            if target_uid:
                ok, msg = _ban_user(target_uid, ban_days)
                if not ok:
                    db.session.rollback()
                    return resp_json(PARAMS_ERROR, msg, data=None)
                actions.append("封禁责任人（%s）" % msg)
                disposed_uid = target_uid
                ban_msg = msg
        if not actions:
            return resp_json(PARAMS_ERROR, "未选择任何处置动作", data=None)
        # 通知举报人：处理结果
        create_notification(
            r.reporter_id, 'system', title='举报已处理',
            content='你举报的%s已被处理：%s' % (_target_desc(r), '、'.join(actions)),
            related_id=r.id, related_type='report')
        # 通知被处置人：账号封禁 / 群聊频道被解散
        if disposed_uid:
            if r.target_type == 'user':
                d_title = '账号封禁通知'
                d_content = '你的账号因涉嫌违规已被封禁（%s）。' % ban_msg if ban_msg else '你的账号收到一条违规处理提醒。'
            else:
                seg = []
                if disband:
                    seg.append('你创建的%s因涉嫌违规已被解散' % _target_desc(r))
                if ban_msg:
                    seg.append('你的账号已被封禁（%s）' % ban_msg)
                d_title = '违规处理通知'
                d_content = '，'.join(seg) + '。'
            create_notification(disposed_uid, 'system', title=d_title, content=d_content,
                                related_id=r.id, related_type='report')
        r.status = 1
        r.handle_note = (data.get('note') or '')[:500] or ('；'.join(actions))[:500]
        r.handled_by = session.get('admin_id')
        r.handled_at = datetime.now(timezone.utc)
        db.session.commit()
        return resp_json(OK, "处置完成：" + "、".join(actions), data=_report_dict(r, with_reporter=False))
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"处置失败：{str(e)}", data=None)


# ============================ 用户/对象 直接管理（辅助处置） ============================
@bp.route('/api/users', methods=['GET'])
@admin_required
def api_users():
    """搜索用户：?q=昵称/邮箱/编号  &limit="""
    try:
        kw = (request.args.get('q') or '').strip()
        limit = min(int(request.args.get('limit', 30) or 30), 100)
        q = User.query.filter(User.deleted_at.is_(None))
        if kw:
            like = f"%{kw}%"
            q = q.filter(db.or_(User.nickname.like(like), User.email.like(like), User.number.like(like)))
        rows = q.order_by(User.id.desc()).limit(limit).all()
        return resp_json(OK, "ok", data={"users": [{
            "id": u.id, "nickname": u.nickname, "number": u.number, "email": u.email,
            "status": u.status,
            "banned_until": u.banned_until.strftime('%Y-%m-%d %H:%M:%S') if u.banned_until else None,
            "created_at": u.created_at.strftime('%Y-%m-%d %H:%M:%S') if u.created_at else None,
        } for u in rows]})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"查询失败：{str(e)}", data=None)


@bp.route('/api/user/ban', methods=['POST'])
@admin_required
def api_user_ban():
    """封禁/解封用户：{user_id, days:int}。days<0 表示立即解封。"""
    data = request.get_json(silent=True) or {}
    uid = data.get('user_id')
    try:
        days = int(data.get('days', 0) or 0)
    except (TypeError, ValueError):
        return resp_json(PARAMS_ERROR, "days 非法", data=None)
    u = User.query.get(uid) if uid else None
    if not u or u.deleted_at is not None:
        return resp_json(PARAMS_ERROR, "用户不存在", data=None)
    if days < 0:
        u.status = 1
        u.banned_until = None
        db.session.commit()
        return resp_json(OK, "已解封", data={"user_id": uid, "status": 1})
    ok, msg = _ban_user(uid, days)
    return (resp_json(OK, msg, data={"user_id": uid, "status": 0}) if ok
            else resp_json(PARAMS_ERROR, msg, data=None))


# ============================ 用户想法 / 反馈 ============================
_FB_CATEGORY = {'idea': '想法', 'suggestion': '建议', 'bug': '问题', 'other': '其他'}
_FB_STATUS = {0: '待处理', 1: '已回复', 2: '已归档'}


def _feedback_dict(f, with_user=True):
    try:
        images = json.loads(f.images) if f.images else []
    except Exception:
        images = []
    u = User.query.get(f.user_id) if with_user else None
    pub = Admin.query.get(f.replied_by) if (with_user and f.replied_by) else None
    return {
        "id": f.id,
        "category": f.category or 'idea',
        "category_label": _FB_CATEGORY.get(f.category, '想法'),
        "content": f.content,
        "images": images,
        "status": f.status if f.status is not None else 0,
        "status_label": _FB_STATUS.get(f.status, '待处理'),
        "reply": f.reply,
        "replied_at": f.replied_at.strftime('%Y-%m-%d %H:%M:%S') if f.replied_at else None,
        "replied_by": ((pub.nickname or pub.username) if pub else None),
        "created_at": f.created_at.strftime('%Y-%m-%d %H:%M:%S') if f.created_at else None,
        "user": ({"id": u.id, "nickname": u.nickname, "number": u.number, "email": u.email, "avatar": u.avatar}
                 if u else {"id": f.user_id}),
    }


@bp.route('/api/feedbacks', methods=['GET'])
@admin_required
def api_feedbacks():
    """接收用户提交的想法/反馈：?status=0|1|2&limit=30&offset=0（待处理排前）"""
    try:
        status = request.args.get('status')
        limit = min(int(request.args.get('limit', 30) or 30), 100)
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        q = Feedback.query
        if status in ('0', '1', '2'):
            q = q.filter(Feedback.status == int(status))
        total = q.count()
        rows = q.order_by(Feedback.status.asc(), Feedback.id.desc()).offset(offset).limit(limit).all()
        return resp_json(OK, "ok", data={"total": total, "feedbacks": [_feedback_dict(f) for f in rows]})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"获取反馈失败：{str(e)}", data=None)


@bp.route('/api/feedback/reply', methods=['POST'])
@admin_required
def api_feedback_reply():
    """处理反馈：{feedback_id, action:'reply'|'archive', reply}
    reply 会写入站内通知告知提交人；archive 仅归档不通知。"""
    data = request.get_json(silent=True) or {}
    f = Feedback.query.get(data.get('feedback_id'))
    if not f:
        return resp_json(PARAMS_ERROR, "反馈不存在", data=None)
    action = (data.get('action') or 'reply').strip()
    reply = (data.get('reply') or '').strip()
    if action == 'archive':
        f.status = 2
        f.replied_by = session.get('admin_id')
        f.replied_at = datetime.now(timezone.utc)
        db.session.commit()
        return resp_json(OK, "已归档", data=_feedback_dict(f, with_user=False))
    if not reply:
        return resp_json(PARAMS_ERROR, "回复内容不能为空", data=None)
    if len(reply) > 1000:
        reply = reply[:1000]
    admin = _current_admin()
    f.reply = reply
    f.status = 1
    f.replied_by = admin.id if admin else None
    f.replied_at = datetime.now(timezone.utc)
    create_notification(
        f.user_id, 'system', title='你的想法收到了',
        content='管理员回复了你的想法：%s' % reply[:120],
        related_id=f.id, related_type='feedback')
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"回复失败：{str(e)}", data=None)
    # 在线用户即时反馈（前端收到后可刷新通知徒章）
    try:
        socketio.emit('feedback_replied', {"feedback_id": f.id}, room=str(f.user_id))
    except Exception:
        pass
    return resp_json(OK, "已回复", data=_feedback_dict(f, with_user=False))


# ============================ 全站公告 ============================
def _announcement_dict(a):
    try:
        media = json.loads(a.media) if a.media else []
    except Exception:
        media = []
    pub = Admin.query.get(a.publisher_id) if a.publisher_id else None
    return {
        "id": a.id,
        "title": a.title,
        "content": a.content,
        "media": [m for m in media if isinstance(m, dict) and m.get('url')],
        "status": a.status if a.status is not None else 1,
        "publisher": ((pub.nickname or pub.username) if pub else None),
        "read_count": SiteAnnouncementRead.query.filter_by(announcement_id=a.id).count(),
        "created_at": a.created_at.strftime('%Y-%m-%d %H:%M:%S') if a.created_at else None,
    }


@bp.route('/api/announcements', methods=['GET'])
@admin_required
def api_announcements():
    """公告列表：?limit=30&offset=0（含已下架）"""
    try:
        limit = min(int(request.args.get('limit', 30) or 30), 100)
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        q = SiteAnnouncement.query
        if request.args.get('status') in ('0', '1'):
            q = q.filter(SiteAnnouncement.status == int(request.args.get('status')))
        total = q.count()
        rows = q.order_by(SiteAnnouncement.id.desc()).offset(offset).limit(limit).all()
        return resp_json(OK, "ok", data={"total": total, "announcements": [_announcement_dict(a) for a in rows]})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"获取公告失败：{str(e)}", data=None)


@bp.route('/api/announcement/media_upload', methods=['POST'])
@admin_required
def api_announcement_media_upload():
    """公告配图/视频：form-data file → {url, type(2视频/3图片), name}
    存到 photo|video/announcement/site/{uuid}.ext"""
    try:
        f = request.files.get('file')
        if not f or not f.filename:
            return resp_json(PARAMS_ERROR, "缺少文件", data=None)
        if get_file_size(f) is None:
            return resp_json(PARAMS_ERROR, "文件过大（上限 100MB）", data=None)
        saved = save_upload_file(f, scope=('announcement', 'site'))
        if not saved:
            return resp_json(PARAMS_ERROR, "文件保存失败（仅支持图片与常见视频格式）", data=None)
        url, file_type, filename = saved
        if file_type not in (2, 3):
            return resp_json(PARAMS_ERROR, "公告仅支持图片或视频文件", data=None)
        db.session.commit()
        return resp_json(OK, "上传成功", data={"url": url, "type": file_type, "name": filename})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"上传失败：{str(e)}", data=None)


@bp.route('/api/announcement/create', methods=['POST'])
@admin_required
def api_announcement_create():
    """发布全站公告：{title(必填), content, media:[{type,url,name}]} → 入库 + SocketIO 广播给所有在线用户
    （离线用户下次进入由 /announcement/pending 补发）"""
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    if not title:
        return resp_json(PARAMS_ERROR, "公告标题不能为空", data=None)
    if len(title) > 120:
        title = title[:120]
    media_in = data.get('media') or []
    if not isinstance(media_in, list):
        media_in = []
    media_out = []
    for m in media_in[:9]:
        if not isinstance(m, dict) or not str(m.get('url') or '').strip():
            continue
        try:
            mt = int(m.get('type') or 3)
        except (TypeError, ValueError):
            mt = 3
        media_out.append({"type": mt if mt in (2, 3) else 3, "url": str(m['url']).strip()[:500],
                          "name": (str(m.get('name') or '').strip())[:128]})
    a = SiteAnnouncement(
        title=title, content=content or None,
        media=json.dumps(media_out, ensure_ascii=False) if media_out else None,
        publisher_id=session.get('admin_id'), status=1,
    )
    try:
        db.session.add(a)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"发布失败：{str(e)}", data=None)
    payload = {
        "id": a.id, "title": a.title, "content": a.content, "media": media_out,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    try:
        socketio.emit('site_announcement', payload)
    except Exception as e:
        current_app.logger.exception(f"公告广播失败：{e}")
    return resp_json(OK, "公告已发布", data={"announcement": _announcement_dict(a), "push": payload})


@bp.route('/api/announcement/offline', methods=['POST'])
@admin_required
def api_announcement_offline():
    """下架公告：{announcement_id, offline:0/1}，下架后不再向用户端推送（已知晓记录保留）"""
    data = request.get_json(silent=True) or {}
    a = SiteAnnouncement.query.get(data.get('announcement_id'))
    if not a:
        return resp_json(PARAMS_ERROR, "公告不存在", data=None)
    a.status = 0 if data.get('offline', 1) else 1
    db.session.commit()
    return resp_json(OK, "已下架" if a.status == 0 else "已重新发布", data=_announcement_dict(a))


@bp.route('/api/announcement/delete', methods=['POST'])
@admin_required
def api_announcement_delete():
    """删除公告：连同用户的已知晓记录与已上传的图片/视频一并清除"""
    data = request.get_json(silent=True) or {}
    a = SiteAnnouncement.query.get(data.get('announcement_id'))
    if not a:
        return resp_json(PARAMS_ERROR, "公告不存在", data=None)
    try:
        media = json.loads(a.media) if a.media else []
    except Exception:
        media = []
    SiteAnnouncementRead.query.filter_by(announcement_id=a.id).delete()
    db.session.delete(a)
    db.session.commit()
    for m in media:
        if isinstance(m, dict) and m.get('url'):
            try:
                delete_old_file(m['url'])
            except Exception:
                pass
    return resp_json(OK, "已删除", data=None)
