from decorators import login_required
from exts import db,cache
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for, current_app, g
from models.user import User
from models.upload import BgHistory
from presence import describe as presence_describe
import secrets
import datetime
from utils import generate_unique_number, delete_old_file, resp_json, refresh_user_ban

bp = Blueprint('user',__name__,url_prefix='/user')

def _gen_captcha(length=4):
    """生成加密安全的纯数字验证码"""
    return "".join(secrets.choice("0123456789") for _ in range(length))

@bp.route('/register',methods=['POST','GET'])
def register():
    try:
        if request.method =="GET":
            # 注册表单已内嵌在登录页（翻转切换），直达 /user/register 时带 mode 标记过去并自动展开注册
            return redirect(url_for('user.login', mode='register'))
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"code":400,"msg":"请求格式错误，需要JSON","data":None}),400
        email = (data.get('email') or "").strip()
        code = data.get('email_code')
        nickname = (data.get('nickname') or "").strip()
        password = data.get('password')
        # ===== 基础参数校验 =====
        if not email or not code or not nickname or not password:
            return jsonify({"code":400,"msg":"邮箱、验证码、昵称、密码均不能为空","data":None}),400
        # ===== 邮箱验证码校验（必须存在且匹配，防止不传验证码绕过） =====
        mail_code = cache.get(f"res:{email}")
        if not mail_code or str(code) != str(mail_code):
            return jsonify({"code":400,"msg":"邮箱验证码错误或已过期","data":None}),400
        cache.delete(f"res:{email}")  # 验证码一次性消费
        if User.query.filter_by(email=email).first():
            return jsonify({"code":400,"msg":"该邮箱已被注册","data":None}),400
        number = generate_unique_number(type="user_")
        avatar_url = f"https://robohash.org/{email}?set=set4&size=200x200"
        user = User(number=number, email=email, nickname=nickname, avatar=avatar_url, password=password)
        db.session.add(user)
        db.session.commit()
        return jsonify({
            "code":0,
            "msg":"注册成功",
            "data":{
                "number":number,
                "email":email,
                "nickname":nickname,
                "avatar":avatar_url
            }
        }),200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code":500,
            "msg":f"注册失败:{str(e)}",
            "data":None
        }),500

@bp.route('/login',methods=['POST','GET'])
def login():
    try:
        if request.method =="GET":
            return render_template("login.html")
        data = request.get_json()
        if not data:
            return jsonify({"msg":"请求数据为空"}),400
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({"msg":"请输入邮箱或密码"}),400
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            return jsonify({"msg":"用户不存在或者密码错误"}),401
        # 定时封禁到期自动解封（封禁只有到期后才能登录）
        refresh_user_ban(user)
        # 拦截已注销/被封禁账号
        if user.deleted_at is not None or user.status != 1:
            return jsonify({"msg":"账号已被封禁或已注销"}),403
        # 单设备登录：每次登录生成新 token 写入 Redis（按 user_id 只存最新一个），
        # 旧设备的 cookie 里还是旧 token，请求时在 hooks.before_request 比对失败即被挤下线
        login_token = secrets.token_hex(16)
        cache.set(f'login_token:{user.id}', login_token, timeout=30 * 24 * 3600)  # 30 天，与登录态同生命周期
        session['user_id'] = user.id
        session['login_token'] = login_token
        return jsonify({
            "msg":"登录成功",
            "code":0,
            "data":{
                "id": user.id,
                "number": user.number,
                "email": user.email,
                "nickname": user.nickname,
                "avatar": user.avatar,
                "status": user.status,
                "signature":user.Signature,
                "background": user.background,
                "theme": user.theme or 'dark',
                "global_bg": user.global_bg,
                "bg_opacity": user.bg_opacity,
                "gender": user.gender
            }
            }),200
    except Exception as e:
        return jsonify({"msg":f"服务器内部错误:{str(e)}"}),500

@bp.route('/logout')
def logout():
    uid = session.get('user_id')
    if uid is not None:
        cache.delete(f'login_token:{uid}')  # 清除服务端 token，退出后旧 cookie 彻底失效
    session.clear()
    return jsonify({"msg":"登出成功"}),200

@bp.route('/me', methods=['GET'])
@login_required
def me():
    """SPA 刷新后获取当前登录用户信息"""
    user = g.user
    return jsonify({
        "code": 0,
        "msg": "ok",
        "data": {
            "id": user.id,
            "number": user.number,
            "email": user.email,
            "nickname": user.nickname,
            "avatar": user.avatar,
            "status": user.status,
            "signature": user.Signature,
            "background": user.background,
            "theme": user.theme or 'dark',
            "global_bg": user.global_bg,
            "bg_opacity": user.bg_opacity,
            "gender": user.gender,
            "profile_visibility": user.profile_visibility or 1,
        }
    }), 200

@bp.route('/profile/<int:uid>', methods=['GET'])
@login_required
def user_profile(uid):
    """他人公开资料卡：昵称/头像/Ping号/签名 + 好友关系状态（评论区点名字跳转用）"""
    from models.contact import friends, FriendRequest
    user = User.query.get(uid)
    if not user or user.status != 1:
        return jsonify({"code": 404, "msg": "用户不存在或已停用", "data": None}), 404
    is_friend = db.session.query(friends.c.user_id).filter(
        friends.c.user_id == g.user.id, friends.c.friend_id == uid).first() is not None
    pending = FriendRequest.query.filter_by(
        sender_id=g.user.id, receiver_id=uid, status=0).first() is not None
    # 我给对方设置的备注（仅好友且已设置时非空），用于资料卡展示“备注 + 原生昵称”
    rkrow = db.session.query(friends.c.remark).filter(
        friends.c.user_id == g.user.id, friends.c.friend_id == uid).first()
    remark = rkrow.remark if rkrow else None
    pres = presence_describe(user)
    is_self = user.id == g.user.id
    # 邮箱仅本人/好友可见（隐私）；资料卡“我的动态”是否可见由对方 profile_visibility + 关系决定（基础信息始终展示）
    pv = user.profile_visibility or 1
    if is_self:
        moments_visible = True
    elif pv == 3:
        moments_visible = False
    elif pv == 2:
        moments_visible = is_friend
    else:
        moments_visible = True
    return jsonify({
        "code": 0,
        "msg": "ok",
        "data": {
            "id": user.id,
            "number": user.number,
            "nickname": user.nickname,
            "remark": remark,
            "avatar": user.avatar,
            "background": user.background,
            "signature": user.Signature,
            "gender": user.gender,
            "email": user.email if (is_self or is_friend) else None,
            "is_self": is_self,
            "is_friend": is_friend,
            "request_pending": pending,
            "profile_visibility": pv,
            "moments_visible": moments_visible,
            "online": pres["online"],
            "last_online": pres["last_online"],
            "presence_text": pres["presence_text"],
        }
    }), 200

@bp.route("/mail/register/captcha",methods=['POST','GET'])
def register_mail_captcha():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "code":400,
                "msg":"请求格式错误，需要JSON"
            }),400
        email = data.get('email')
        captcha = _gen_captcha()
        body = f"【Ping】您正在注册账号,验证码是{captcha},5分钟内有效"
        current_app.celery.send_task("send_mail",(email,"Chatonline",body))
        cache.set(f"res:{email}",captcha,timeout=300)  # 验证码 和 邮箱 关联，设置关联时间300s，也就是5min
        return jsonify({
            "code":0,
            "msg":"验证码发送成功",
            "data":None
        }),200
    except Exception as e:
        return jsonify({
            "code":500,
            "msg":f"生成验证码错误:{str(e)}",
            "data":None
        }),500
@bp.route('/mail/update_passwd/captcha',methods=['POST'])
def update_passwd_mail_captcha():
    """改密码/忘记密码-第一步：发验证码。
    免登录可调（前端传 email）；已登录且不传 email 时默认发给本人绑定邮箱。"""
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get('email') or "").strip()
        cu = getattr(g, 'user', None)
        if not email:
            if cu is None:
                return jsonify({"code": 400, "msg": "请填写注册邮箱", "data": None}), 400
            email = cu.email
        user = User.query.filter_by(email=email).first()
        if not user or user.deleted_at is not None or user.status != 1:
            return jsonify({"code": 400, "msg": "该邮箱未注册或账号异常", "data": None}), 400
        # 注意：找回密码以“接收邮箱”为判定标准，不校验登录态是否本人——
        # 能收到该邮箱的验证码即有权重置，攻击者收不到码就无法重置，无需额外限制
        # 60s 重发间隔，防止邮件轰炸
        if cache.get(f"pwd_cd:{email}"):
            return jsonify({"code": 400, "msg": "验证码已发送，请稍后再试", "data": None}), 400
        captcha = _gen_captcha()
        body = f"【Ping】您正在修改密码,验证码是{captcha},请确保您是本人,验证码5分钟内有效"
        current_app.celery.send_task("send_mail", (email, "Chatonline", body))
        cache.set(f"pwd:{email}", captcha, timeout=300)  # 验证码 和 邮箱 关联，设置关联时间300s，也就是5min
        cache.set(f"pwd_cd:{email}", 1, timeout=60)
        return jsonify({
            "code": 0,
            "msg": "验证码发送成功",
            "data": None
        }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"生成验证码错误:{str(e)}",
            "data": None
        }), 500

@bp.route('/update/password', methods=['POST', 'GET'])
def update_password():
    """改密码/忘记密码-第二步：验码并更新。
    免登录可调（前端传 email）；已登录时只能改自己。"""
    try:
        if request.method == "GET":
            return render_template('update_passwd.html')
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "code": 400,
                "msg": "请求格式错误，需要JSON",
                "data": None
            }), 400
        cu = getattr(g, 'user', None)
        email = (data.get('email') or "").strip()
        if not email:
            if cu is None:
                return jsonify({"code": 400, "msg": "请填写注册邮箱", "data": None}), 400
            email = cu.email
        eCode = data.get('eCode')
        new_password = data.get('new_password')
        if not eCode or not new_password:
            return jsonify({"code": 400, "msg": "验证码和新密码不能为空", "data": None}), 400
        if len(str(new_password)) < 6:
            return jsonify({"code": 400, "msg": "密码至少 6 位", "data": None}), 400
        user = User.query.filter_by(email=email).first()
        if not user or user.deleted_at is not None or user.status != 1:
            return jsonify({"code": 400, "msg": "该邮箱未注册或账号异常", "data": None}), 400
        # 同上：以邮箱验证码为准，不校验登录态是否本人
        captcha = cache.get(f"pwd:{email}")
        # 验证码不存在/过期，或不匹配：直接拒绝，不删除缓存（避免输错一次就作废）
        if not captcha or str(eCode) != str(captcha):
            return jsonify({
                "code": 400,
                "msg": "验证码错误或已过期，请重新获取",
                "data": None
            }), 400
        cache.delete(f"pwd:{email}")  # 验证通过后才消费验证码
        cache.delete(f"pwd_cd:{email}")
        user.password = new_password  # setter 自动 generate_password_hash
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "密码修改成功",
            "data": None
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": f"服务器内部错误{str(e)}，请稍后重试",
            "data": None
        }), 500

# ---------------- 背景图历史（全局背景 / 资料页封面）：设置 / 恢复默认 / 历史列表 ----------------
_BG_KINDS = ('global', 'profile')
_BG_KEEP = 6  # 每人每类最多保留张数（含当前），超出自动删最旧

def _user_public_dict(user):
    return {
        "id": user.id, "number": user.number, "email": user.email,
        "nickname": user.nickname, "avatar": user.avatar, "status": user.status,
        "signature": user.Signature, "background": user.background,
        "theme": user.theme or 'dark', "global_bg": user.global_bg,
        "bg_opacity": user.bg_opacity, "gender": user.gender,
        "profile_visibility": user.profile_visibility or 1,
    }

@bp.route('/bg/history', methods=['GET'])
@login_required
def bg_history():
    kind = request.args.get('kind')
    if kind not in _BG_KINDS:
        return resp_json(code=400, msg="kind 参数非法", data=None)
    rows = BgHistory.query.filter_by(user_id=g.user.id, kind=kind) \
        .order_by(BgHistory.created_at.desc(), BgHistory.id.desc()).all()
    current = g.user.global_bg if kind == 'global' else g.user.background
    return resp_json(code=0, msg="获取成功", data={
        "kind": kind,
        "current": current,
        "items": [{
            "id": r.id, "url": r.url,
            "created_at": r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else None,
        } for r in rows],
    })

@bp.route('/bg/set', methods=['POST'])
@login_required
def bg_set():
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    url = (data.get('url') or '').strip()
    if kind not in _BG_KINDS or not url:
        return resp_json(code=400, msg="参数错误", data=None)
    user = g.user
    if kind == 'global':
        user.global_bg = url
    else:
        user.background = url
    now = datetime.datetime.now(datetime.timezone.utc)
    # upsert 历史：已存在同 url 则刷新时间到最新（复用旧图不被当作最旧清理），否则新增
    exist = BgHistory.query.filter_by(user_id=user.id, kind=kind, url=url).first()
    if exist:
        exist.created_at = now
    else:
        db.session.add(BgHistory(user_id=user.id, kind=kind, url=url, created_at=now))
    db.session.flush()
    # 裁剪到最新 6 张，超出删除记录并清理磁盘文件（当前 url 永不删）
    allrows = BgHistory.query.filter_by(user_id=user.id, kind=kind) \
        .order_by(BgHistory.created_at.desc(), BgHistory.id.desc()).all()
    for extra in allrows[_BG_KEEP:]:
        db.session.delete(extra)
        if extra.url != url:
            try:
                delete_old_file(extra.url)
            except Exception:
                pass
    db.session.commit()
    return resp_json(code=0, msg="背景已更新", data=_user_public_dict(user))

@bp.route('/bg/clear', methods=['POST'])
@login_required
def bg_clear():
    data = request.get_json(silent=True) or {}
    kind = data.get('kind')
    if kind not in _BG_KINDS:
        return resp_json(code=400, msg="参数错误", data=None)
    user = g.user
    if kind == 'global':
        user.global_bg = None
    else:
        user.background = None
    db.session.commit()
    return resp_json(code=0, msg="已恢复默认背景", data=_user_public_dict(user))

#------------------------------------------------------------------------------------------
@bp.route('/update/personal_info', methods=['POST', 'GET'])
@login_required
def update_personal_info():
    try:
        if request.method == "GET":
            return render_template('personal_info.html')
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "code": 400,
                "msg": "请求格式错误，需要JSON"
            }), 400
        nickname = data.get('nickname')
        signature = data.get('signature')
        background = data.get('background')
        theme = data.get('theme')
        global_bg = data.get('global_bg')
        bg_opacity = data.get('bg_opacity')
        gender = data.get('gender')
        profile_visibility = data.get('profile_visibility')
        user = g.user
        if nickname is not None:
            user.nickname = nickname
        if signature is not None:
            user.Signature = signature
        if background is not None:
            # 空字符串表示清除背景；其他值为 /upload/image 返回的图片 URL
            user.background = background or None
        if theme in ('light', 'dark'):
            user.theme = theme
        if global_bg is not None:
            # 空字符串表示清除全局背景；外观设置接口与资料接口共用
            user.global_bg = global_bg or None
        if bg_opacity is not None:
            # 全局背景磨砂层不透明度，钳制在 10-90 之间避免完全透明/不可见
            try:
                user.bg_opacity = max(10, min(90, int(bg_opacity)))
            except (TypeError, ValueError):
                pass
        if gender is not None:
            # 性别 0=未知 1=男 2=女，非法值忽略
            try:
                gv = int(gender)
                if gv in (0, 1, 2):
                    user.gender = gv
            except (TypeError, ValueError):
                pass
        if profile_visibility is not None:
            # 资料卡“我的动态”可见范围 1=所有人 2=仅好友 3=仅自己，非法值忽略
            try:
                pv = int(profile_visibility)
                if pv in (1, 2, 3):
                    user.profile_visibility = pv
            except (TypeError, ValueError):
                pass
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg":"修改信息成功",
            "data":{
                "id": user.id,
                "number": user.number,
                "email": user.email,
                "nickname": user.nickname,
                "avatar": user.avatar,
                "status": user.status,
                "signature": user.Signature,
                "background": user.background,
                "theme": user.theme or 'dark',
                "global_bg": user.global_bg,
                "bg_opacity": user.bg_opacity,
                "gender": user.gender,
                "profile_visibility": user.profile_visibility or 1
            }
        }),200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": f"服务器内部错误{str(e)}，请稍后重试",
            "data": None
        }), 500