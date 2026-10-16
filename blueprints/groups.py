from flask import Blueprint,request,jsonify,g
import datetime
import json
from decorators import login_required
from models.group import GroupMessage, GroupAnnouncement,Group,GroupMember,AnnouncementUserIsRead, GroupInvite, GroupOwnerTransfer, GroupJoinRequest
from models.bot import BotTemplate, GroupBot
from bots.engine import run_bots
from utils import generate_unique_number, resp_json, save_upload_file, guard_sensitive, delete_old_file, get_file_size, touch_group_conversation, upsert_conversation, create_notification, quote_preview
from models.upload import GroupFile
from models.user import User
from exts import db, socketio

#导入参数
from utils import PARAMS_ERROR,OK,SERVER_ERROR
#--------------------------------------------------------------------------
def select_member_role(group_id):
    member = GroupMember.query.filter_by(user_id=g.user.id,group_id=group_id).first()
    if not member:
        return None
    return member.role
#--------------------------------------------------------------------------
# 身份/权限类（当前用户是谁、有没有权限）→ 永远用 g.user，不信任前端传的 user_id
# 业务/资源类（操作哪个群、帖子、目标用户、消息内容）→ 前端传参，明确告诉后端操作对象
#--------------------------------------------------------------------------
bp = Blueprint('groups',__name__,url_prefix='/group')

@bp.route('/create', methods=['POST'])
@login_required
def create_group():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求格式错误，需要JSON", "data": None}), 400
        owner_id = g.user.id
        name = data.get('group_name')
        if not name or not name.strip():  #  .strip():去掉字符串首位的空白   这里的意思是判断name是否权威空白
            return jsonify({"code": 400, "msg": "群组名称不能为空", "data": None}), 400
        number = generate_unique_number(type="group_")
        name = name.strip()     # 去除空白
        description = data.get('group_desc')
        max_members = data.get('max_members', 200)
        # 前端上传过头像就用上传的，没传则默认 robohash
        avatar_url = data.get('avatar') or f"https://robohash.org/{number}?set=set2&size=200×200"
        group = Group(owner_id=owner_id,number=number,name=name,description=description,max_members=max_members,avatar=avatar_url)
        db.session.add(group)
        db.session.flush()
        # 群主加入成员表
        member = GroupMember(group_id=group.id,user_id=owner_id,role=1,nickname=None)
        db.session.add(member)
        # 创建即出现在群主的会话列表，不等第一条消息
        upsert_conversation(owner_id, 'group', group.id, None, '群已创建')
        # 创建时选择的 Bot：批量绑定（仅绑定存在的模板）
        for bid in (data.get('bot_template_ids') or []):
            try:
                bid = int(bid)
            except (TypeError, ValueError):
                continue
            if BotTemplate.query.get(bid):
                db.session.add(GroupBot(group_id=group.id, bot_template_id=bid, enabled=1))
        db.session.commit()

        return jsonify({
            "code": 200,
            "msg": "创建群组成功",
            "data": {
                "id": group.id,
                "owner_id": owner_id,
                "number": number,
                "name": name,
                "description": group.description,
                "avatar": avatar_url,
                "max_members": group.max_members,
                "status": group.status
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": f"服务器内部出错: {str(e)}",
            "data": None
        }), 500
@bp.route('/join', methods=['POST'])
@login_required
def join_group():
    """自助申请入群（搜索/编号入口）：不再即时加入，而是创建待审核申请，需群主/管理员通过。
    幂等：已是成员返回 already；已有待审申请返回 pending；否则创建并通知全体管理员。"""
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="前端传入信息错误",data=None)
        group_id = data.get('group_id')
        message = (data.get('message') or "").strip()[:200] or None
        user_id = g.user.id
        if not group_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少群号",data=None)
        group = Group.query.filter_by(id=group_id).first()
        if not group:
            return resp_json(code=PARAMS_ERROR,msg="该群聊不存在",data=None)
        if group.status != 1:
            return resp_json(code=PARAMS_ERROR,msg="该群聊已解散",data=None)
        if GroupMember.query.filter_by(user_id=user_id,group_id=group_id).first():
            # 已在群：幂等返回，让前端直接打开会话；顺带自愈缺失的会话记录
            upsert_conversation(user_id, 'group', group_id, None, '已加入群聊')
            db.session.commit()
            return resp_json(msg="已在群聊中", data={
                "group_id": group_id, "user_id": user_id,
                "nickname": g.user.nickname, "already": True
            })
        if GroupJoinRequest.query.filter_by(group_id=group_id, user_id=user_id, status=0).first():
            return resp_json(msg="申请审核中，请耐心等待", data={
                "group_id": group_id, "pending": True
            })
        if GroupMember.query.filter_by(group_id=group_id).count() >= group.max_members:
            return resp_json(code=PARAMS_ERROR,msg="群人数已满，无法加入",data=None)
        # 入群方式=直接加入（join_need_verify=0）：跳过审核直接入群并触发欢迎
        if not group.join_need_verify:
            db.session.add(GroupMember(group_id=group_id, user_id=user_id, role=3, nickname=None))
            upsert_conversation(user_id, 'group', group_id, None, '已加入群聊')
            db.session.commit()
            run_bots('group', group_id, 'member_join', 'post',
                     {'sender_id': user_id, 'sender_name': g.user.nickname})
            return resp_json(msg="已加入群聊", data={
                "group_id": group_id, "user_id": user_id, "joined": True
            })
        req = GroupJoinRequest(group_id=group_id, user_id=user_id, message=message, status=0)
        db.session.add(req)
        db.session.flush()
        # 通知全体群主/管理员（role 1/2）有新的入群申请
        managers = GroupMember.query.filter(
            GroupMember.group_id == group_id, GroupMember.role.in_([1, 2])).all()
        content = f"{g.user.nickname} 申请加入「{group.name}」"
        for m in managers:
            create_notification(m.user_id, 'group_join_request', title='入群申请',
                                content=content, related_id=req.id, related_type='group_join_request')
            socketio.emit('notification', {
                'type': 'group_join_request', 'title': '入群申请', 'content': content,
                'related_id': req.id, 'related_type': 'group_join_request'
            }, room=str(m.user_id))
        db.session.commit()
        return resp_json(msg="入群申请已发送，等待群主/管理员审核", data={
            "group_id": group_id, "request_id": req.id, "requested": True
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误: {str(e)}",data=None)


@bp.route('/join/requests', methods=['GET'])
@login_required
def list_join_requests():
    """我管理的群（群主/管理员）收到的待处理入群申请"""
    try:
        me = g.user.id
        managed = [row.group_id for row in GroupMember.query.filter(
            GroupMember.user_id == me, GroupMember.role.in_([1, 2])).with_entities(GroupMember.group_id).all()]
        if not managed:
            return resp_json(OK, "获取成功", data={"requests": [], "total": 0})
        reqs = GroupJoinRequest.query.filter(
            GroupJoinRequest.group_id.in_(managed), GroupJoinRequest.status == 0
        ).order_by(GroupJoinRequest.created_at.desc()).all()
        group_ids = list({r.group_id for r in reqs})
        user_ids = list({r.user_id for r in reqs})
        groups = {gr.id: gr for gr in Group.query.filter(Group.id.in_(group_ids)).all()} if group_ids else {}
        users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
        result = []
        for r in reqs:
            gr = groups.get(r.group_id); us = users.get(r.user_id)
            if not gr or gr.status != 1:
                continue
            result.append({
                "request_id": r.id, "group_id": r.group_id, "group_name": gr.name, "group_avatar": gr.avatar,
                "user_id": r.user_id, "user_nickname": us.nickname if us else f"用户 {r.user_id}",
                "user_avatar": us.avatar if us else None, "message": r.message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return resp_json(OK, "获取成功", data={"requests": result, "total": len(result)})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/join/approve', methods=['POST'])
@login_required
def approve_join_request():
    """通过入群申请：{request_id} → 正式入群并通知申请人"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        request_id = data.get('request_id')
        if not request_id:
            return resp_json(PARAMS_ERROR, "缺少 request_id", data=None)
        req = GroupJoinRequest.query.get(request_id)
        if not req or req.status != 0:
            return resp_json(PARAMS_ERROR, "申请不存在或已被处理", data=None)
        # 处理人必须是该群群主/管理员
        operator = GroupMember.query.filter_by(group_id=req.group_id, user_id=g.user.id).first()
        if not operator or operator.role not in (1, 2):
            return resp_json(PARAMS_ERROR, "无权处理该申请", data=None)
        group = Group.query.filter_by(id=req.group_id).first()
        if not group or group.status != 1:
            req.status = 2; req.handled_by = g.user.id; req.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(PARAMS_ERROR, "群聊已不存在或已解散", data=None)
        if GroupMember.query.filter_by(group_id=req.group_id, user_id=req.user_id).first():
            req.status = 1; req.handled_by = g.user.id; req.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(OK, "该用户已在群中", data={"group_id": req.group_id, "already": True})
        if GroupMember.query.filter_by(group_id=req.group_id).count() >= group.max_members:
            return resp_json(PARAMS_ERROR, "群人数已满，无法通过", data=None)
        db.session.add(GroupMember(group_id=req.group_id, user_id=req.user_id, role=3, nickname=None))
        upsert_conversation(req.user_id, 'group', req.group_id, None, '已加入群聊')
        req.status = 1; req.handled_by = g.user.id; req.handled_at = _utcnow_naive()
        applicant = User.query.get(req.user_id)
        content = f"你加入「{group.name}」的申请已通过"
        create_notification(req.user_id, 'group_join_request', title='入群申请已通过',
                            content=content, related_id=req.id, related_type='group_join_request')
        db.session.commit()
        socketio.emit('notification', {
            'type': 'group_join_request', 'title': '入群申请已通过', 'content': content,
            'related_id': req.id, 'related_type': 'group_join_request', 'group_id': req.group_id
        }, room=str(req.user_id))
        # 新成员正式入群：触发自动欢迎
        run_bots('group', req.group_id, 'member_join', 'post',
                 {'sender_id': req.user_id, 'sender_name': applicant.nickname if applicant else None})
        return resp_json(OK, "已通过申请", data={
            "group_id": req.group_id, "user_id": req.user_id,
            "user_nickname": applicant.nickname if applicant else None
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/join/reject', methods=['POST'])
@login_required
def reject_join_request():
    """拒绝入群申请：{request_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        request_id = data.get('request_id')
        if not request_id:
            return resp_json(PARAMS_ERROR, "缺少 request_id", data=None)
        req = GroupJoinRequest.query.get(request_id)
        if not req or req.status != 0:
            return resp_json(PARAMS_ERROR, "申请不存在或已被处理", data=None)
        operator = GroupMember.query.filter_by(group_id=req.group_id, user_id=g.user.id).first()
        if not operator or operator.role not in (1, 2):
            return resp_json(PARAMS_ERROR, "无权处理该申请", data=None)
        req.status = 2; req.handled_by = g.user.id; req.handled_at = _utcnow_naive()
        group = Group.query.get(req.group_id)
        content = f"你加入「{group.name}」的申请被拒绝" if group else "你的入群申请被拒绝"
        create_notification(req.user_id, 'group_join_request', title='入群申请被拒绝',
                            content=content, related_id=req.id, related_type='group_join_request')
        db.session.commit()
        socketio.emit('notification', {
            'type': 'group_join_request', 'title': '入群申请被拒绝', 'content': content,
            'related_id': req.id, 'related_type': 'group_join_request'
        }, room=str(req.user_id))
        return resp_json(OK, "已拒绝申请", data={"request_id": req.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)

@bp.route('/leave', methods=['POST'])
@login_required
def leave_group():
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="前端传递信息错误",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少群组信息",data=None)
        member = GroupMember.query.filter_by(user_id=g.user.id, group_id=group_id).first()
        if not member:
            return resp_json(code=PARAMS_ERROR,msg="你不在该群聊中，无法退出",data=None)
        if member.role == 1:
            return resp_json(code=PARAMS_ERROR,msg="群主不可直接退出群聊,可解散/转让",data=None)
        db.session.delete(member)
        db.session.commit()
        # 退群通知：通知群主/管理员
        run_bots('group', group_id, 'member_leave', 'post',
                 {'sender_id': g.user.id, 'sender_name': g.user.nickname})
        return resp_json(msg="退出群组成功",data={
            "group_id": group_id,
            "user_id": g.user.id
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误{str(e)}",data=None)

@bp.route('/owner/disband', methods=['POST'])
@login_required
def disband_group():

    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="前端传递信息错误",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少群组编号",data=None)
        member = GroupMember.query.filter_by(user_id=g.user.id,group_id=group_id).first()
        if not member or member.role != 1:
            return resp_json(code=PARAMS_ERROR,msg="仅限群主操作",data=None)
        group = Group.query.filter_by(id=group_id,owner_id=g.user.id).first()
        if not group:
            return resp_json(code=PARAMS_ERROR,msg="群聊不存在或你不拥有该群",data=None)
        # 先删依赖公告的子表，再删公告/消息/文件/成员，最后删群本身
        AnnouncementUserIsRead.query.filter_by(group_id=group_id).delete()
        GroupAnnouncement.query.filter_by(group_id=group_id).delete()
        GroupMessage.query.filter_by(group_id=group_id).delete()
        GroupFile.query.filter_by(group_id=group_id).delete()
        GroupMember.query.filter_by(group_id=group_id).delete()
        db.session.delete(group)
        db.session.commit()
        return resp_json(msg="解散群聊成功")
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误{str(e)}",data=None)

# 已移除旧的 /owner/owned 即时移交接口：它绕过“被转让人确认”流程，与 /transfer 待确认机制冲突（前端未调用）

@bp.route('/publish/file', methods=['POST'])
@login_required
def group_publish_file():
    try:
        files = request.files.getlist("file")  # ← 关键：getlist , 获取多个文件
        if not files or len(files) == 0:
            return resp_json(PARAMS_ERROR, "所上传文件为空", data=None)

        group_id = request.form.get("group_id", type=int)
        if not group_id:
            return resp_json(PARAMS_ERROR, "缺失群组编号", data=None)
        # 必须是群成员才能上传群文件
        member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
        if not member:
            return resp_json(PARAMS_ERROR, "你不是群成员", data=None)
        # 禁言校验（与文本发言一致）：个人禁言到期前不可发；全员禁言时普通成员不可发（群主/管理员豁免）
        now = datetime.datetime.utcnow()
        if member.mute_until and member.mute_until > now:
            return resp_json(PARAMS_ERROR, "你已被禁言，暂时无法发言", data=None)
        group_obj = Group.query.filter_by(id=group_id).first()
        if group_obj and group_obj.mute_all and member.role not in (1, 2):
            return resp_json(PARAMS_ERROR, "群已开启全员禁言", data=None)

        # Bot 前置规则（防垃圾图片/防刷屏/夜间禁言等）：按扩展名预判本次是否为图片消息，命中则拦截（不落盘不存库）
        from utils import ALLOWED_IMAGE_EXTENSIONS
        def _is_image(fn):
            ext = fn.rsplit('.', 1)[1].lower() if fn and '.' in fn else ''
            return ext in ALLOWED_IMAGE_EXTENSIONS
        any_image = any(_is_image(f.filename) for f in files if f.filename)
        blocked, bmsg = run_bots('group', group_id, 'message', 'pre',
                                 {'sender_id': g.user.id, 'sender_name': g.user.nickname,
                                  'text': '', 'msg_type': 2 if any_image else 3})
        if blocked:
            return resp_json(PARAMS_ERROR, bmsg, data=None)

        results = []
        for file in files:
            if not file.filename:  # 跳过空文件项
                continue
            file_size = get_file_size(file)
            if file_size is None:
                db.session.rollback()
                return resp_json(PARAMS_ERROR, "文件超出大小限制", data=None)
            result = save_upload_file(file, scope=('group', group_id))
            if result is None:
                db.session.rollback()
                return resp_json(PARAMS_ERROR, "文件格式不支持或保存失败", data=None)
            file_url, file_type, filename = result
            # 存群文件记录（含 file_size）：每个文件一条，与消息无关，群文件列表按记录展示
            group_file = GroupFile(
                group_id=group_id,
                file_url=file_url,
                file_type=file_type,
                filename=filename,
                file_size=file_size,
                sender_id=g.user.id
            )
            db.session.add(group_file)
            db.session.flush()  # 取自增 file_id
            results.append({
                "file_id": group_file.id,
                "file_url": file_url,
                "file_type": file_type,
                "filename": filename,
                "file_size": file_size
            })

        if not results:
            db.session.rollback()
            return resp_json(PARAMS_ERROR, "所上传文件为空", data=None)

        # 合并成一条消息：单文件→图片(2)/文件(3)；多文件→混排媒体消息(4)，
        # content 存 JSON 数组，前端一个气泡内渲染图/视频/文件卡片（Telegram 相册风）
        if len(results) == 1:
            r0 = results[0]
            msg_type = 2 if r0["file_type"] == 3 else 3
            content = r0["file_url"]
            summary = "[图片]" if msg_type == 2 else f"[文件] {r0['filename']}"
        else:
            msg_type = 4
            content = json.dumps(
                [{"url": x["file_url"], "type": x["file_type"], "name": x["filename"], "size": x["file_size"]} for x in results],
                ensure_ascii=False)
            summary = f"[相册] {len(results)} 个文件"
        message = GroupMessage(
            sender_id=g.user.id, group_id=group_id,
            content=content, msg_type=msg_type
        )
        db.session.add(message)

        # 刷新全体群成员会话列表（除发送者未读+1）
        touch_group_conversation(group_id, message.id, summary, g.user.id)
        db.session.commit()

        # 实时广播：一条消息只广播一次（带发送者名字/头像，前端气泡直接渲染）
        socketio.emit("receive_message", {
            "id": message.id, "sender_id": g.user.id, "group_id": group_id,
            "sender_name": g.user.nickname, "sender_avatar": g.user.avatar,
            "content": content, "msg_type": msg_type,
            "filename": results[0]["filename"] if len(results) == 1 else None,
            "file_size": results[0]["file_size"] if len(results) == 1 else None
        }, room=f"group_{group_id}")

        return resp_json(msg="文件上传成功", data={
            "group_id": group_id,
            "files": results,
            "msg_id": message.id,
            "count": len(results)
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误{str(e)}", data=None)

@bp.route('/permission/set', methods=['POST'])
@login_required
def group_permission_set():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端传递参数错误",data=None)
        group_id = data.get('group_id')
        action = data.get('action')
        if not group_id:
            return resp_json(PARAMS_ERROR,"缺少群组编号",data=None)
        # 仅群主可设置/取消管理员（身份取 g.user，不信任前端）
        if select_member_role(group_id) != 1:
            return resp_json(PARAMS_ERROR,"仅群主可管理管理员",data=None)
        target_member_id = data.get('target_member_id')
        if target_member_id == g.user.id:
            return resp_json(PARAMS_ERROR,"群主不可以对自己操作")
        if not target_member_id:
            return resp_json(PARAMS_ERROR,"缺少群成员编号",data=None)
        target_member = GroupMember.query.filter_by(user_id=target_member_id,group_id=group_id).first()
        if not target_member:
            return resp_json(PARAMS_ERROR,"非群成员",data=None)
        if action == "promote":
            if target_member.role == 2:
                return resp_json(PARAMS_ERROR,"此用户已是管理员",data=None)
            target_member.role = 2
            msg="设置管理员成功"
        else:
            if target_member.role != 2:
                return resp_json(PARAMS_ERROR,"该用户已经为普通群成员",data=None)
            target_member.role = 3
            msg ="管理员降级成功"
        db.session.commit()
        return resp_json(msg=msg, data={
            "target_member_id": target_member_id,
            "action": action,
            "role": target_member.role
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)
@bp.route('/publish/announcement', methods=['POST'])
@login_required
def publish_group_announcement():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端传入信息错误",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(PARAMS_ERROR,"缺少群组信息",data=None)
        content = data.get('content')
        if not content:
            return resp_json(PARAMS_ERROR,"缺少公告信息",data=None)
        user_role = select_member_role(group_id)
        if user_role == 3 or user_role is None:
            return resp_json(PARAMS_ERROR,"无权发布公告或非群成员",data=None)
        ok, smsg = guard_sensitive(content, g.user.id, 'group', group_id)
        if not ok:
            return resp_json(PARAMS_ERROR, smsg, data=None)
        announcement = GroupAnnouncement(group_id=group_id,publisher_id=g.user.id,content=content)
        db.session.add(announcement)
        db.session.flush()
        anno_id = announcement.id
        anno_is_read = AnnouncementUserIsRead(user_id=g.user.id,announcement_id=anno_id,group_id=group_id)
        db.session.add(anno_is_read)
        db.session.commit()
        return resp_json(msg="发布公告成功", data={
            "announcement_id": anno_id,
            "group_id": group_id,
            "content": content,
            "publisher_id": g.user.id
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)

@bp.route('/announcement/latest', methods=['POST'])
@login_required
def latest_group_announcement():
    """打开群聊时拉取：仅置顶全群最新的一条公告；若该条已被本人知晓则不再置顶（不回退到旧公告，无则 data=None）"""
    try:
        data = request.get_json()
        group_id = (data or {}).get('group_id')
        if not group_id:
            return resp_json(PARAMS_ERROR, "缺少群组信息", data=None)
        if not GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first():
            return resp_json(PARAMS_ERROR, "你不是群成员", data=None)
        # 只取全群最新一条公告（不分读/未读）
        anno = GroupAnnouncement.query.filter_by(group_id=group_id).order_by(GroupAnnouncement.id.desc()).first()
        if not anno:
            return resp_json(OK, data=None)
        # 该最新公告本人已知晓则不再置顶，避免回退显示旧公告
        if AnnouncementUserIsRead.query.filter_by(user_id=g.user.id, announcement_id=anno.id).first():
            return resp_json(OK, data=None)
        publisher = User.query.get(anno.publisher_id)
        return resp_json(OK, data={
            "announcement_id": anno.id,
            "group_id": group_id,
            "content": anno.content,
            "publisher_id": anno.publisher_id,
            "publisher_nickname": publisher.nickname if publisher else '未知',
            "created_at": anno.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误,{str(e)}", data=None)

@bp.route('/announcement/get', methods=['POST'])
@login_required
def read_group_announcement():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端数据传输为空",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(PARAMS_ERROR,"缺少群组信息",data=None)
        announcement_id = data.get('announcement_id')
        if not announcement_id:
            return resp_json(PARAMS_ERROR,"缺少公告编号",data=None)
        if AnnouncementUserIsRead.query.filter_by(announcement_id=announcement_id,user_id=g.user.id).first():
            return resp_json(OK, data=None)  # 幂等：重复上报视为知晓成功
        anno = AnnouncementUserIsRead(user_id=g.user.id,announcement_id=announcement_id,group_id=group_id)
        db.session.add(anno)
        db.session.commit()
        return resp_json(msg="插入公告已读表成功", data={
            "announcement_id": anno.id,
            "group_id": group_id,
            "user_id": g.user.id
        })
    except Exception as e:
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)


@bp.route('/<int:group_id>/messages', methods=['GET'])  # bp 前缀已是 /group，这里再写 /group 会变成 /group/group/... 导致404
@login_required
def get_group_messages(group_id):
    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")

    before_id = request.args.get('before_id', type=int)  #before_id 就是一个"我当前看到的最早一条消息的 id"，告诉后端："别再给我这条和之后的了，给我这条之前的。"
    per_page = request.args.get('per_page', 20, type=int)  # 每次加载消息的数量,20为默认值，如果前端没有传递per_page默认就用20

    query = GroupMessage.query.filter_by(group_id=group_id)
    if before_id:
        query = query.filter(GroupMessage.id < before_id)  #把前端传递的before_id 和 消息id 做对比，返回比before_id 编号小的Query 对象

    messages = query.order_by(GroupMessage.id.desc()).limit(per_page).all()
#   降序查询GroupMessage的id,然后只查询20条，然后返回
    messages.reverse()  # Python列表方法，反向输出
    msg_list = []
    # 文件消息（msg_type=3）的 content 就是文件 URL，混排相册（msg_type=4）的 content 是 JSON 数组，
    # 都按 URL 关联 GroupFile 补齐原始文件名/大小，否则前端只能从 URL 猜名字
    urls_of = {}
    for m in messages:
        if m.msg_type == 3 and m.content:
            urls_of.setdefault(m.content, []).append(m)
        elif m.msg_type == 4 and m.content:
            try:
                for it in json.loads(m.content):
                    if it.get("url"): urls_of.setdefault(it["url"], []).append(m)
            except Exception:
                pass
    fmap = {}
    if urls_of:
        for f in GroupFile.query.filter(GroupFile.file_url.in_(list(urls_of.keys()))).all():
            fmap[f.file_url] = f
    # 引用回复：批量读取本群内被引用消息（昵称+摘要），避免逐条查询
    rids = {m.reply_to for m in messages if m.reply_to}
    ref_map = {}
    if rids:
        for ref in GroupMessage.query.filter(GroupMessage.id.in_(list(rids))).all():
            rf = fmap.get(ref.content) if ref.msg_type == 3 else None
            ref_map[ref.id] = {
                "nickname": ref.sender.nickname if ref.sender else "未知用户",
                "preview": quote_preview(ref.msg_type, ref.content, rf.filename if rf else None,
                                         deleted=bool(ref.deleted_at)),
            }
    for m in messages:
        gf = fmap.get(m.content) if m.msg_type == 3 else None
        mentions = [int(x) for x in m.mentions.split(',') if x.strip().isdigit()] if m.mentions else []
        ref = ref_map.get(m.reply_to) if m.reply_to else None
        msg_list.append({
            "msg_id": m.id,
            "sender_id": m.sender_id,
            "sender_name": m.sender.nickname if m.sender else "未知用户",
            "sender_avatar": m.sender.avatar if m.sender else None,
            "content": "[已撤回]" if m.deleted_at else m.content,
            # 存量旧软删数据强制按文本渲染，避免前端拿“[已撤回]”当文件 URL 去渲染文件卡片
            "msg_type": 1 if m.deleted_at else m.msg_type,
            "mentions": mentions,
            "filename": gf.filename if gf else None,
            "file_size": gf.file_size if gf else None,
            "reply_to": m.reply_to,
            "reply_to_nickname": ref["nickname"] if ref else None,
            "reply_to_content": ref["preview"] if ref else None,
            "created_at": m.created_at.isoformat() if m.created_at else None
        })

    return resp_json(OK, "获取成功", data={
        "messages": msg_list,
        "has_more": len(messages) == per_page
    })

@bp.route('/<int:group_id>/members', methods=['GET'])
@login_required
def get_group_members(group_id):
    # 1. 校验当前用户是不是群成员
    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")

    # 2. 查该群所有成员
    members = GroupMember.query.filter_by(group_id=group_id).all()

    # 3. 拼数据
    member_list = []
    for m in members:
        user = User.query.get(m.user_id) #ORM模型.query.get(主键):  按主键查一条，直接返回对象
        if not user:
            continue
        member_list.append({
            "user_id": user.id,
            "nickname": user.nickname,
            "avatar_url": user.avatar,
            "role": m.role,          # 1=群主 2=管理员 3=普通成员
            "role_name": {1: "群主", 2: "管理员", 3: "成员"}.get(m.role, "成员"),
            "mute_until": m.mute_until.isoformat() if m.mute_until else None,
            "joined_at": m.joined_at.isoformat() if hasattr(m, 'joined_at') and m.joined_at else None
        })

    group = Group.query.get(group_id)
    return resp_json(OK, "获取成功", data={
        "members": member_list,
        "total": len(member_list),
        "my_role": member.role,
        "mute_all": (group.mute_all if group else 0) or 0,
        "join_need_verify": (group.join_need_verify if group and group.join_need_verify is not None else 1),
        "name": group.name if group else None,
        "description": group.description if group else None,
        "avatar": group.avatar if group else None,
        "number": group.number if group else None
    })


@bp.route('/<int:group_id>/peek', methods=['GET'])
@login_required
def group_peek(group_id):
    """群聊轻预览卡：供搜索结果点击群名时使用，返回简介/人数/成员性别构成"""
    from sqlalchemy import func
    group = Group.query.filter_by(id=group_id, status=1).first()
    if not group:
        return resp_json(PARAMS_ERROR, "群不存在或已解散")
    rows = db.session.query(User.gender, func.count(GroupMember.id)) \
        .join(GroupMember, GroupMember.user_id == User.id) \
        .filter(GroupMember.group_id == group_id) \
        .group_by(User.gender).all()
    dist = {0: 0, 1: 0, 2: 0}
    for gv, c in rows:
        dist[gv if gv is not None else 0] = c
    total = sum(dist.values())
    joined = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first() is not None
    pending = GroupJoinRequest.query.filter_by(group_id=group_id, user_id=g.user.id, status=0).first() is not None
    return resp_json(OK, "ok", data={
        "group_id": group.id, "name": group.name, "avatar": group.avatar,
        "description": group.description, "number": group.number,
        "member_count": total, "joined": joined, "pending": pending,
        "gender": {"unknown": dist[0], "male": dist[1], "female": dist[2]}
    })


@bp.route('/info', methods=['PUT'])
@login_required
def update_group_info():
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return resp_json(PARAMS_ERROR, "group_id 不能为空")

    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")
    if member.role not in (1, 2):
        return resp_json(PARAMS_ERROR, "只有群主或管理员能修改群信息")

    group = Group.query.get(group_id)
    if not group:
        return resp_json(PARAMS_ERROR, "群不存在")

    data = request.get_json()
    if not data:
        return resp_json(PARAMS_ERROR, "请求体不能为空")

    changed = False

    name = data.get('name')
    if name is not None:
        name = name.strip()
        if not name or len(name) > 50:
            return resp_json(PARAMS_ERROR, "群名称长度需在1-50之间")
        group.name = name
        changed = True

    avatar_url = data.get('avatar_url')
    if avatar_url is not None:
        group.avatar = avatar_url.strip()
        changed = True

    description = data.get('description')
    if description is not None:
        if len(description) > 200:
            return resp_json(PARAMS_ERROR, "简介不能超过200字")
        group.description = description
        changed = True

    if not changed:
        return resp_json(PARAMS_ERROR, "没有可修改的字段")

    db.session.commit()

    return resp_json(OK, "修改成功", data={
        "group_id": group.id,
        "name": group.name,
        "avatar_url": group.avatar,
        "description": group.description
    })
@bp.route('/transfer', methods=['PUT'])
@login_required
def transfer_group_owner():
    """
    发起群主转让申请（需被转让人确认后才生效）
    :return:
    """
    # 1. group_id 从 URL Query 取
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return resp_json(code=PARAMS_ERROR, msg="group_id 不能为空", data=None)

    # 2. 校验当前用户是群主
    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(code=PARAMS_ERROR, msg="你不是群成员", data=None)
    if member.role != 1:
        return resp_json(code=PARAMS_ERROR, msg="只有群主才能转让群", data=None)

    # 3. new_owner_id 从 JSON body 取
    data = request.get_json(silent=True)
    if not data:
        return resp_json(code=PARAMS_ERROR, msg="请求体不能为空", data=None)

    new_owner_id = data.get('new_owner_id')
    if not new_owner_id:
        return resp_json(code=PARAMS_ERROR, msg="new_owner_id 不能为空", data=None)

    # 4. 校验目标用户是群成员且非群主
    target = GroupMember.query.filter_by(group_id=group_id, user_id=new_owner_id).first()
    if not target:
        return resp_json(code=PARAMS_ERROR, msg="对方不是群成员", data=None)
    if target.role == 1:
        return resp_json(code=PARAMS_ERROR, msg="对方已经是群主", data=None)

    # 不能转给自己
    if new_owner_id == g.user.id:
        return resp_json(code=PARAMS_ERROR, msg="不能转给自己", data=None)

    # 5. 同一成员已有待处理申请时不重复发起（群主可改对其他成员重新发起）
    if GroupOwnerTransfer.query.filter_by(group_id=group_id, to_id=new_owner_id, status=0).first():
        return resp_json(code=PARAMS_ERROR, msg="已向对方发起转让申请，等待处理", data=None)

    # 6. 创建待确认申请，不变更角色
    group = Group.query.get(group_id)
    transfer = GroupOwnerTransfer(group_id=group_id, from_id=g.user.id, to_id=new_owner_id, status=0)
    db.session.add(transfer)
    db.session.flush()
    create_notification(new_owner_id, 'group_owner_transfer', title='群主转让邀请',
                        content=f"{g.user.nickname} 请求将「{group.name if group else '群聊'}」的群主转让给你，请确认",
                        related_id=transfer.id, related_type='group_owner_transfer')
    db.session.commit()
    socketio.emit('notification', {
        'type': 'group_owner_transfer', 'title': '群主转让邀请',
        'content': f"{g.user.nickname} 请求将「{group.name if group else '群聊'}」的群主转让给你，请确认",
        'related_id': transfer.id, 'related_type': 'group_owner_transfer'
    }, room=str(new_owner_id))
    return resp_json(code=OK, msg="转让申请已发送，等待对方确认", data={
        "transfer_id": transfer.id,
        "group_id": group_id,
        "new_owner_id": new_owner_id
    })


@bp.route('/owner/transfers', methods=['GET'])
@login_required
def list_owner_transfers():
    """我收到的待处理群主转让申请"""
    try:
        me = g.user.id
        transfers = GroupOwnerTransfer.query.filter_by(to_id=me, status=0) \
            .order_by(GroupOwnerTransfer.created_at.desc()).all()
        group_ids = [t.group_id for t in transfers]
        from_ids = [t.from_id for t in transfers]
        groups = {gr.id: gr for gr in Group.query.filter(Group.id.in_(group_ids)).all()} if group_ids else {}
        senders = {u.id: u for u in User.query.filter(User.id.in_(from_ids)).all()} if from_ids else {}
        result = []
        for t in transfers:
            gr = groups.get(t.group_id)
            sender = senders.get(t.from_id)
            result.append({
                "transfer_id": t.id,
                "group_id": t.group_id,
                "group_name": gr.name if gr else "未知群聊",
                "group_avatar": gr.avatar if gr else None,
                "from_id": t.from_id,
                "from_nickname": sender.nickname if sender else f"用户 {t.from_id}",
                "created_at": t.created_at.isoformat() if t.created_at else None,
            })
        return resp_json(code=OK, msg="获取成功", data={"transfers": result, "total": len(result)})
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


@bp.route('/owner/transfer/accept', methods=['POST'])
@login_required
def accept_owner_transfer():
    """接受群主转让：{transfer_id} → 双方角色变更并更新 groups.owner_id"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传入信息错误", data=None)
        transfer_id = data.get('transfer_id')
        if not transfer_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 transfer_id", data=None)
        transfer = GroupOwnerTransfer.query.get(transfer_id)
        if not transfer or transfer.to_id != g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="转让申请不存在或无权处理", data=None)
        if transfer.status != 0:
            return resp_json(code=PARAMS_ERROR, msg="该申请已被处理", data=None)
        group = Group.query.filter_by(id=transfer.group_id).first()
        if not group or group.status != 1:
            transfer.status = 2
            transfer.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(code=PARAMS_ERROR, msg="群聊已不存在或已解散", data=None)
        old_member = GroupMember.query.filter_by(group_id=group.id, user_id=transfer.from_id).first()
        new_member = GroupMember.query.filter_by(group_id=group.id, user_id=transfer.to_id).first()
        # 发起人在等待期间已不再是群主（再次转让/被踢等），申请作废
        if not old_member or old_member.role != 1:
            transfer.status = 2
            transfer.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(code=PARAMS_ERROR, msg="发起人已不是群主，转让无效", data=None)
        if not new_member:
            transfer.status = 2
            transfer.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(code=PARAMS_ERROR, msg="你已不在群中，转让无效", data=None)
        # 正式转让：原群主降为普通成员，新群主升为 role=1，同步更新群的 owner_id
        old_member.role = 3
        new_member.role = 1
        group.owner_id = transfer.to_id
        transfer.status = 1
        transfer.handled_at = _utcnow_naive()
        # 回写系统通知给原群主（落库，离线也能看到）
        create_notification(transfer.from_id, 'group_owner_transfer', title='群主转让已接受',
                            content=f"{g.user.nickname} 已接受群主转让，你现在是「{group.name}」的普通成员",
                            related_id=transfer.id, related_type='group_owner_transfer')
        db.session.commit()
        # 通知原群主转让已接受
        socketio.emit('notification', {
            'type': 'group_owner_transfer', 'title': '群主转让已接受',
            'content': f"{g.user.nickname} 已接受群主转让，你现在是「{group.name}」的普通成员",
            'related_id': transfer.id, 'related_type': 'group_owner_transfer'
        }, room=str(transfer.from_id))
        # 本端刷新会话列表，角色变化在下次打开群信息时生效
        return resp_json(code=OK, msg="已成为群主", data={"group_id": group.id, "group_name": group.name})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


@bp.route('/owner/transfer/reject', methods=['POST'])
@login_required
def reject_owner_transfer():
    """拒绝群主转让：{transfer_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传入信息错误", data=None)
        transfer_id = data.get('transfer_id')
        if not transfer_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 transfer_id", data=None)
        transfer = GroupOwnerTransfer.query.get(transfer_id)
        if not transfer or transfer.to_id != g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="转让申请不存在或无权处理", data=None)
        if transfer.status != 0:
            return resp_json(code=PARAMS_ERROR, msg="该申请已被处理", data=None)
        transfer.status = 2
        transfer.handled_at = _utcnow_naive()
        # 通知发起人被拒绝
        group = Group.query.get(transfer.group_id)
        create_notification(transfer.from_id, 'group_owner_transfer', title='群主转让被拒绝',
                            content=f"{g.user.nickname} 拒绝了你发起的群主转让"
                                    + (f"（「{group.name}」）" if group else ""),
                            related_id=transfer.id, related_type='group_owner_transfer')
        db.session.commit()
        socketio.emit('notification', {
            'type': 'group_owner_transfer', 'title': '群主转让被拒绝',
            'content': f"{g.user.nickname} 拒绝了你发起的群主转让"
                       + (f"（「{group.name}」）" if group else ""),
            'related_id': transfer.id, 'related_type': 'group_owner_transfer'
        }, room=str(transfer.from_id))
        return resp_json(code=OK, msg="已拒绝转让", data={"transfer_id": transfer.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
def _hard_delete_group_message(message):
    """硬删除群消息（硬性规则：撤回/删除即从库中消失）：
    媒体消息连带清理 GroupFile 记录与磁盘文件，返回被删消息所属 group_id"""
    urls = []
    if message.msg_type in (2, 3) and message.content:
        urls = [message.content]
    elif message.msg_type == 4 and message.content:
        try:
            urls = [i.get('url') for i in json.loads(message.content) if i.get('url')]
        except Exception:
            urls = []
    if urls:
        for gf in GroupFile.query.filter(GroupFile.file_url.in_(urls)).all():
            db.session.delete(gf)
    gid = message.group_id
    db.session.delete(message)
    db.session.commit()
    for u in urls:
        delete_old_file(u)  # 磁盘文件也一并清掉，不留孤儿
    return gid


@bp.route('/message/delete', methods=['POST'])
@login_required
def delete_group_message():
    """
    删除群聊消息（硬删除，连媒体文件一起从磁盘/数据库移除）
    - 只能删除自己发送的消息
    - 管理员可以删除所有成员的消息
    data = {"message_id": 123}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        
        message_id = data.get('message_id')
        if not message_id:
            return resp_json(code=400, msg="缺少 message_id 参数", data=None)
        
        # 查找消息
        message = GroupMessage.query.get(message_id)
        if not message:
            return resp_json(code=404, msg="消息不存在", data=None)
        
        # 验证权限
        member = GroupMember.query.filter_by(user_id=g.user.id, group_id=message.group_id).first()
        if not member:
            return resp_json(code=403, msg="你不是该群成员", data=None)
        
        # 群主或管理员可以删除任意消息，否则只能删除自己的
        if member.role not in (1, 2) and message.sender_id != g.user.id:
            return resp_json(code=403, msg="无权删除该消息", data=None)
        
        # 硬删除 + 广播移除事件，房间内其他成员界面同步删除气泡
        gid = _hard_delete_group_message(message)
        socketio.emit("message_revoked", {"message_id": int(message_id), "group_id": gid}, room=f"group_{gid}")
        
        return resp_json(code=0, msg="消息已删除", data=None)
        
    except Exception as e:
        db.session.rollback()
        print(f"删除群消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/message/withdraw', methods=['POST'])
@login_required
def withdraw_group_message():
    """
    撤回群聊消息
    - 只能撤回自己发送的消息
    - 管理员可以撤回所有成员的消息
    data = {"message_id": 123}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        
        message_id = data.get('message_id')
        if not message_id:
            return resp_json(code=400, msg="缺少 message_id 参数", data=None)
        
        # 查找消息
        message = GroupMessage.query.get(message_id)
        if not message:
            return resp_json(code=404, msg="消息不存在", data=None)
        
        # 验证权限
        member = GroupMember.query.filter_by(user_id=g.user.id, group_id=message.group_id).first()
        if not member:
            return resp_json(code=403, msg="你不是该群成员", data=None)
        
        # 群主或管理员可以撤回任意消息，否则只能撤回自己的
        if member.role not in (1, 2) and message.sender_id != g.user.id:
            return resp_json(code=403, msg="无权撤回该消息", data=None)

        # 撤回时限：普通成员仅能撤回自己 2 分钟内的消息；管理员/群主不受限
        if member.role == 3:
            now = datetime.datetime.now(datetime.timezone.utc)
            created = message.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            if created is not None and (now - created).total_seconds() > 120:
                return resp_json(code=400, msg="超过 2 分钟，无法撤回", data=None)

        # 保存原内容供撤回后重新编辑（前端回填输入框用）
        original_content = message.content if message.msg_type == 1 else None

        # 硬删除：撤回即消息从库里消失，媒体文件连带清理，并广播移除事件
        gid = _hard_delete_group_message(message)
        socketio.emit("message_revoked", {"message_id": int(message_id), "group_id": gid}, room=f"group_{gid}")

        return resp_json(code=0, msg="消息已撤回", data={
            "original_content": original_content,  # 文本消息回填输入框；媒体消息为 None
            "message_id": message_id
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"撤回群消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/message/edit', methods=['POST'])
@login_required
def edit_group_message():
    """
    编辑撤回的群消息或重新发送的消息
    - 需要 message_id 和新的 content
    - 只能编辑自己发送的消息
    - 管理员可以编辑任意消息
    data = {
        "message_id": 123,
        "content": "新的消息内容"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        
        message_id = data.get('message_id')
        new_content = data.get('content')
        
        if not all([message_id, new_content]):
            return resp_json(code=400, msg="缺少必要参数", data=None)
        
        # 查找消息
        message = GroupMessage.query.get(message_id)
        if not message:
            return resp_json(code=404, msg="消息不存在", data=None)
        
        # 验证权限
        member = GroupMember.query.filter_by(user_id=g.user.id, group_id=message.group_id).first()
        if not member:
            return resp_json(code=403, msg="你不是该群成员", data=None)
        
        # 群主或管理员可以编辑任意消息，否则只能编辑自己的
        if member.role not in (1, 2) and message.sender_id != g.user.id:
            return resp_json(code=403, msg="无权编辑该消息", data=None)
        
        # 更新内容
        message.content = new_content
        
        db.session.commit()
        
        return resp_json(code=0, msg="消息已编辑", data={
            "message_id": message_id,
            "content": message.content
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"编辑群消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/<int:group_id>/files', methods=['GET'])
@login_required
def get_group_files(group_id):
    """获取群文件列表（群文件界面）：仅群成员可见"""
    try:
        member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
        if not member:
            return resp_json(PARAMS_ERROR, "你不是群成员", data=None)
        files = GroupFile.query.filter_by(group_id=group_id).order_by(GroupFile.id.desc()).all()
        file_list = []
        for f in files:
            sender = User.query.get(f.sender_id)
            file_list.append({
                "file_id": f.id,
                "filename": f.filename,
                "file_url": f.file_url,
                "file_type": f.file_type,
                "sender_id": f.sender_id,
                "sender_name": sender.nickname if sender else "未知用户",
                "created_at": f.created_at.isoformat() if f.created_at else None
            })
        return resp_json(OK, "获取成功", data={"files": file_list, "total": len(file_list)})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/file/delete', methods=['POST'])
@login_required
def delete_group_file():
    """
    删除群文件（仅群文件界面）：从磁盘移除实体文件并删除表记录
    - 普通成员只能删除自己上传的文件
    - 群主/管理员(role 1/2)可删除任意文件
    data = {"file_id": 123}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        file_id = data.get('file_id')
        if not file_id:
            return resp_json(code=400, msg="缺少 file_id 参数", data=None)
        group_file = GroupFile.query.get(file_id)
        if not group_file:
            return resp_json(code=404, msg="文件不存在", data=None)
        member = GroupMember.query.filter_by(group_id=group_file.group_id, user_id=g.user.id).first()
        if not member:
            return resp_json(code=403, msg="你不是该群成员", data=None)
        # 群主/管理员可删任意，否则只能删自己上传的
        if member.role not in (1, 2) and group_file.sender_id != g.user.id:
            return resp_json(code=403, msg="无权删除该文件", data=None)
        # 先删磁盘实体文件，再删表记录
        delete_old_file(group_file.file_url)
        db.session.delete(group_file)
        db.session.commit()
        return resp_json(code=0, msg="文件已删除", data={"file_id": int(file_id)})
    except Exception as e:
        db.session.rollback()
        print(f"删除群文件失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
# 邀请 / 踢人 / 禁言 / 全员禁言
def _utcnow_naive():
    """与库中 created_at 一致：MySQL DATETIME 存的是去时区的 UTC 朴素时间"""
    return datetime.datetime.utcnow()


def _can_manage(operator_role, target_role):
    """群主(1)可管理管理员/成员；管理员(2)仅可管理普通成员(3)"""
    if operator_role == 1:
        return target_role in (2, 3)
    if operator_role == 2:
        return target_role == 3
    return False


@bp.route('/invite', methods=['POST'])
@login_required
def invite_group_member():
    """邀请成员加入（需被邀请人确认）：{group_id, invitee_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        group_id = data.get('group_id')
        invitee_id = data.get('invitee_id')
        if not group_id or not invitee_id:
            return resp_json(PARAMS_ERROR, "缺少 group_id 或 invitee_id", data=None)
        invitee_id = int(invitee_id)
        me = g.user.id
        if invitee_id == me:
            return resp_json(PARAMS_ERROR, "不能邀请自己", data=None)
        group = Group.query.filter_by(id=group_id).first()
        if not group or group.status != 1:
            return resp_json(PARAMS_ERROR, "群聊不存在或已解散", data=None)
        # 邀请人必须是群成员
        if not GroupMember.query.filter_by(group_id=group_id, user_id=me).first():
            return resp_json(PARAMS_ERROR, "你不是群成员，无法邀请", data=None)
        invitee = User.query.filter_by(id=invitee_id, status=1).first()
        if invitee is None or invitee.deleted_at is not None:
            return resp_json(PARAMS_ERROR, "被邀请用户不存在", data=None)
        if GroupMember.query.filter_by(group_id=group_id, user_id=invitee_id).first():
            return resp_json(PARAMS_ERROR, "对方已在群中", data=None)
        if GroupInvite.query.filter_by(group_id=group_id, invitee_id=invitee_id, status=0).first():
            return resp_json(PARAMS_ERROR, "已发送邀请，等待对方处理", data=None)
        invite = GroupInvite(group_id=group_id, inviter_id=me, invitee_id=invitee_id, status=0)
        db.session.add(invite)
        db.session.flush()
        create_notification(invitee_id, 'group_invite', title='群聊邀请',
                            content=f"{g.user.nickname} 邀请你加入「{group.name}」",
                            related_id=invite.id, related_type='group_invite')
        db.session.commit()
        socketio.emit('notification', {
            'type': 'group_invite', 'title': '群聊邀请',
            'content': f"{g.user.nickname} 邀请你加入「{group.name}」",
            'related_id': invite.id, 'related_type': 'group_invite'
        }, room=str(invitee_id))
        return resp_json(OK, "邀请已发送", data={"invite_id": invite.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/invites', methods=['GET'])
@login_required
def list_group_invites():
    """我收到的待处理群邀请"""
    try:
        me = g.user.id
        invites = GroupInvite.query.filter_by(invitee_id=me, status=0) \
            .order_by(GroupInvite.created_at.desc()).all()
        group_ids = [i.group_id for i in invites]
        inviter_ids = [i.inviter_id for i in invites]
        groups = {gr.id: gr for gr in Group.query.filter(Group.id.in_(group_ids)).all()} if group_ids else {}
        inviters = {u.id: u for u in User.query.filter(User.id.in_(inviter_ids)).all()} if inviter_ids else {}
        result = []
        for i in invites:
            gr = groups.get(i.group_id)
            inv = inviters.get(i.inviter_id)
            result.append({
                "invite_id": i.id,
                "group_id": i.group_id,
                "group_name": gr.name if gr else "未知群聊",
                "group_avatar": gr.avatar if gr else None,
                "inviter_id": i.inviter_id,
                "inviter_nickname": inv.nickname if inv else f"用户 {i.inviter_id}",
                "created_at": i.created_at.isoformat() if i.created_at else None,
            })
        return resp_json(OK, "获取成功", data={"invites": result, "total": len(result)})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/invite/accept', methods=['POST'])
@login_required
def accept_group_invite():
    """接受群邀请：{invite_id} → 正式入群"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        invite_id = data.get('invite_id')
        if not invite_id:
            return resp_json(PARAMS_ERROR, "缺少 invite_id", data=None)
        invite = GroupInvite.query.get(invite_id)
        if not invite or invite.invitee_id != g.user.id:
            return resp_json(PARAMS_ERROR, "邀请不存在或无权处理", data=None)
        if invite.status != 0:
            return resp_json(PARAMS_ERROR, "该邀请已被处理", data=None)
        group = Group.query.filter_by(id=invite.group_id).first()
        if not group or group.status != 1:
            invite.status = 2
            invite.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(PARAMS_ERROR, "群聊已不存在或已解散", data=None)
        if GroupMember.query.filter_by(group_id=group.id, user_id=g.user.id).first():
            invite.status = 1
            invite.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(OK, "你已在群中", data={"group_id": group.id, "already": True})
        if GroupMember.query.filter_by(group_id=group.id).count() >= group.max_members:
            return resp_json(PARAMS_ERROR, "群人数已满，无法加入", data=None)
        db.session.add(GroupMember(group_id=group.id, user_id=g.user.id, role=3, nickname=g.user.nickname))
        upsert_conversation(g.user.id, 'group', group.id, None, '已加入群聊')
        invite.status = 1
        invite.handled_at = _utcnow_naive()
        db.session.commit()
        # 接受邀请正式入群：触发自动欢迎
        run_bots('group', group.id, 'member_join', 'post',
                 {'sender_id': g.user.id, 'sender_name': g.user.nickname})
        inviter = User.query.get(invite.inviter_id)
        if inviter:
            create_notification(inviter.id, 'group_invite', title='群邀请已接受',
                                content=f"{g.user.nickname} 接受了你的群邀请",
                                related_id=invite.id, related_type='group_invite')
            socketio.emit('notification', {
                'type': 'group_invite', 'title': '群邀请已接受',
                'content': f"{g.user.nickname} 接受了你的群邀请",
                'related_id': invite.id, 'related_type': 'group_invite'
            }, room=str(inviter.id))
        return resp_json(OK, "已加入群聊", data={"group_id": group.id, "group_name": group.name, "group_avatar": group.avatar})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/invite/reject', methods=['POST'])
@login_required
def reject_group_invite():
    """拒绝群邀请：{invite_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        invite_id = data.get('invite_id')
        if not invite_id:
            return resp_json(PARAMS_ERROR, "缺少 invite_id", data=None)
        invite = GroupInvite.query.get(invite_id)
        if not invite or invite.invitee_id != g.user.id:
            return resp_json(PARAMS_ERROR, "邀请不存在或无权处理", data=None)
        if invite.status != 0:
            return resp_json(PARAMS_ERROR, "该邀请已被处理", data=None)
        invite.status = 2
        invite.handled_at = _utcnow_naive()
        db.session.commit()
        return resp_json(OK, "已拒绝邀请", data={"invite_id": invite.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/kick', methods=['POST'])
@login_required
def kick_group_member():
    """踢人：{group_id, target_user_id}，仅群主/管理员，按层级权限"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        group_id = data.get('group_id')
        target_user_id = data.get('target_user_id')
        if not group_id or not target_user_id:
            return resp_json(PARAMS_ERROR, "缺少 group_id 或 target_user_id", data=None)
        target_user_id = int(target_user_id)
        me = g.user.id
        if target_user_id == me:
            return resp_json(PARAMS_ERROR, "不能踢出自己", data=None)
        operator_role = select_member_role(group_id)
        if operator_role not in (1, 2):
            return resp_json(PARAMS_ERROR, "无权限操作", data=None)
        target = GroupMember.query.filter_by(group_id=group_id, user_id=target_user_id).first()
        if not target:
            return resp_json(PARAMS_ERROR, "对方不在群中", data=None)
        if not _can_manage(operator_role, target.role):
            return resp_json(PARAMS_ERROR, "不能踢出同级或更高级成员", data=None)
        kicked = User.query.get(target_user_id)
        db.session.delete(target)
        db.session.commit()
        # 被踢也视为离开：退群通知
        run_bots('group', group_id, 'member_leave', 'post',
                 {'sender_id': target_user_id, 'sender_name': kicked.nickname if kicked else None})
        socketio.emit('notification', {
            'type': 'group_kick', 'title': '你已被移出群聊',
            'content': "你已被群管理员移出群聊",
            'related_id': int(group_id), 'related_type': 'group'
        }, room=str(target_user_id))
        return resp_json(OK, "已踢出成员", data={"group_id": group_id, "target_user_id": target_user_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/mute', methods=['POST'])
@login_required
def mute_group_member():
    """禁言单个成员：{group_id, target_user_id, minutes} minutes=0 解除禁言"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        group_id = data.get('group_id')
        target_user_id = data.get('target_user_id')
        minutes = data.get('minutes', 10)
        if not group_id or not target_user_id:
            return resp_json(PARAMS_ERROR, "缺少 group_id 或 target_user_id", data=None)
        target_user_id = int(target_user_id)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            return resp_json(PARAMS_ERROR, "minutes 需为整数", data=None)
        operator_role = select_member_role(group_id)
        if operator_role not in (1, 2):
            return resp_json(PARAMS_ERROR, "无权限操作", data=None)
        target = GroupMember.query.filter_by(group_id=group_id, user_id=target_user_id).first()
        if not target:
            return resp_json(PARAMS_ERROR, "对方不在群中", data=None)
        if not _can_manage(operator_role, target.role):
            return resp_json(PARAMS_ERROR, "不能禁言同级或更高级成员", data=None)
        if minutes <= 0:
            target.mute_until = None
            db.session.commit()
            return resp_json(OK, "已解除禁言", data={"target_user_id": target_user_id, "mute_until": None})
        until = _utcnow_naive() + datetime.timedelta(minutes=minutes)
        target.mute_until = until
        db.session.commit()
        socketio.emit('notification', {
            'type': 'group_mute', 'title': '你已被禁言',
            'content': f"你在该群已被禁言 {minutes} 分钟",
            'related_id': int(group_id), 'related_type': 'group'
        }, room=str(target_user_id))
        return resp_json(OK, "已禁言", data={"target_user_id": target_user_id, "mute_until": until.isoformat()})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/mute_all', methods=['POST'])
@login_required
def mute_all_group():
    """全员禁言开关：{group_id, enabled}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        group_id = data.get('group_id')
        enabled = data.get('enabled', 1)
        if not group_id:
            return resp_json(PARAMS_ERROR, "缺少 group_id", data=None)
        operator_role = select_member_role(group_id)
        if operator_role not in (1, 2):
            return resp_json(PARAMS_ERROR, "无权限操作", data=None)
        group = Group.query.filter_by(id=group_id).first()
        if not group or group.status != 1:
            return resp_json(PARAMS_ERROR, "群聊不存在或已解散", data=None)
        group.mute_all = 1 if int(enabled) == 1 else 0
        db.session.commit()
        socketio.emit('group_mute_all', {
            'group_id': int(group_id), 'mute_all': group.mute_all, 'operator_id': g.user.id
        }, room=f"group_{group_id}")
        return resp_json(OK, "全员禁言已" + ("开启" if group.mute_all else "关闭"),
                         data={"group_id": group_id, "mute_all": group.mute_all})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/join_mode', methods=['POST'])
@login_required
def set_join_mode():
    """入群方式开关：{group_id, need_verify} 1=需验证审核 0=直接加入（仅群主/管理员）"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "前端传入信息错误", data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(PARAMS_ERROR, "缺少 group_id", data=None)
        if select_member_role(group_id) not in (1, 2):
            return resp_json(PARAMS_ERROR, "无权限操作", data=None)
        group = Group.query.filter_by(id=group_id).first()
        if not group or group.status != 1:
            return resp_json(PARAMS_ERROR, "群聊不存在或已解散", data=None)
        group.join_need_verify = 1 if int(data.get('need_verify', 1)) == 1 else 0
        db.session.commit()
        return resp_json(OK, "入群方式已改为" + ("需验证" if group.join_need_verify else "直接加入"),
                         data={"group_id": group_id, "join_need_verify": group.join_need_verify})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)