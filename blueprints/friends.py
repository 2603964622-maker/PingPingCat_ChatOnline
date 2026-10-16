from flask import Blueprint, request, jsonify, g
import datetime

from decorators import login_required
from exts import db, socketio
from models.contact import friends, FriendRequest, Blacklist
from models.user import User
from utils import resp_json, create_notification
from utils import PARAMS_ERROR, OK, SERVER_ERROR

# 身份永远用 g.user，业务对象（对谁发起申请/处理哪条申请）由前端传参
bp = Blueprint('friends', __name__, url_prefix='/friend')


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _are_friends(a, b):
    return db.session.query(friends).filter(
        friends.c.user_id == a, friends.c.friend_id == b).first() is not None


# --------------------------------------------------------------------------
@bp.route('/request', methods=['POST'])
@login_required
def send_request():
    """发起好友申请：{to_user_id, message}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        to_user_id = data.get('to_user_id')
        message = (data.get('message') or '').strip()[:200] or None
        if not to_user_id:
            return resp_json(PARAMS_ERROR, "缺少 to_user_id", data=None)
        to_user_id = int(to_user_id)
        me = g.user.id
        if to_user_id == me:
            return resp_json(PARAMS_ERROR, "不能添加自己为好友", data=None)
        receiver = User.query.filter_by(id=to_user_id, status=1).first()
        if receiver is None or receiver.deleted_at is not None:
            return resp_json(PARAMS_ERROR, "用户不存在", data=None)
        if _are_friends(me, to_user_id):
            return resp_json(PARAMS_ERROR, "你们已经是好友了", data=None)
        # 已有待处理申请则不重复发起；若此前我拒绝对方，允许再次申请
        exist = FriendRequest.query.filter(
            FriendRequest.sender_id == me,
            FriendRequest.receiver_id == to_user_id,
            FriendRequest.status == 0).first()
        if exist:
            return resp_json(PARAMS_ERROR, "已发送申请，等待对方处理", data=None)
        req = FriendRequest(sender_id=me, receiver_id=to_user_id, message=message, status=0)
        db.session.add(req)
        db.session.flush()
        create_notification(
            to_user_id, 'friend',
            title='新的好友申请',
            content=f"{g.user.nickname} 请求添加你为好友" + (f"：{message}" if message else ''),
            related_id=req.id, related_type='friend_request')
        db.session.commit()
        socketio.emit('notification', {
            'type': 'friend', 'title': '新的好友申请',
            'content': f"{g.user.nickname} 请求添加你为好友",
            'related_id': req.id, 'related_type': 'friend_request'
        }, room=str(to_user_id))
        return resp_json(OK, "好友申请已发送", data={"request_id": req.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/requests', methods=['GET'])
@login_required
def list_requests():
    """我收到的待处理好友申请 + 我发出的待处理申请"""
    try:
        me = g.user.id
        received = FriendRequest.query.filter_by(receiver_id=me, status=0) \
            .order_by(FriendRequest.created_at.desc()).all()
        senders = {u.id: u for u in User.query.filter(
            User.id.in_([r.sender_id for r in received])).all()} if received else {}
        received_list = [{
            "request_id": r.id,
            "sender_id": r.sender_id,
            "sender_nickname": senders[r.sender_id].nickname if r.sender_id in senders else f"用户 {r.sender_id}",
            "sender_avatar": senders[r.sender_id].avatar if r.sender_id in senders else None,
            "message": r.message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in received]

        sent = FriendRequest.query.filter_by(sender_id=me, status=0) \
            .order_by(FriendRequest.created_at.desc()).all()
        receivers = {u.id: u for u in User.query.filter(
            User.id.in_([r.receiver_id for r in sent])).all()} if sent else {}
        sent_list = [{
            "request_id": r.id,
            "receiver_id": r.receiver_id,
            "receiver_nickname": receivers[r.receiver_id].nickname if r.receiver_id in receivers else f"用户 {r.receiver_id}",
            "receiver_avatar": receivers[r.receiver_id].avatar if r.receiver_id in receivers else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in sent]

        return resp_json(OK, "获取成功", data={"received": received_list, "sent": sent_list})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/accept', methods=['POST'])
@login_required
def accept_request():
    """同意好友申请：{request_id} → 双向写入好友表"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        request_id = data.get('request_id')
        if not request_id:
            return resp_json(PARAMS_ERROR, "缺少 request_id", data=None)
        req = FriendRequest.query.get(request_id)
        if not req or req.receiver_id != g.user.id:
            return resp_json(PARAMS_ERROR, "申请不存在或无权处理", data=None)
        if req.status != 0:
            return resp_json(PARAMS_ERROR, "该申请已被处理", data=None)
        req.status = 1
        req.handled_at = _utcnow()
        # 双向插入好友记录（幂等：已存在则忽略）
        for a, b in ((req.sender_id, req.receiver_id), (req.receiver_id, req.sender_id)):
            if not _are_friends(a, b):
                db.session.execute(friends.insert().values(user_id=a, friend_id=b))
        db.session.commit()
        sender = User.query.get(req.sender_id)
        socketio.emit('notification', {
            'type': 'friend', 'title': '好友申请已通过',
            'content': f"{g.user.nickname} 已同意你的好友申请",
            'related_id': req.id, 'related_type': 'friend_request'
        }, room=str(req.sender_id))
        return resp_json(OK, "已添加为好友", data={
            "friend_id": req.sender_id,
            "friend_nickname": sender.nickname if sender else None,
            "friend_avatar": sender.avatar if sender else None,
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/reject', methods=['POST'])
@login_required
def reject_request():
    """拒绝好友申请：{request_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        request_id = data.get('request_id')
        if not request_id:
            return resp_json(PARAMS_ERROR, "缺少 request_id", data=None)
        req = FriendRequest.query.get(request_id)
        if not req or req.receiver_id != g.user.id:
            return resp_json(PARAMS_ERROR, "申请不存在或无权处理", data=None)
        if req.status != 0:
            return resp_json(PARAMS_ERROR, "该申请已被处理", data=None)
        req.status = 2
        req.handled_at = _utcnow()
        db.session.commit()
        return resp_json(OK, "已拒绝该申请", data={"request_id": req.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/delete', methods=['POST'])
@login_required
def delete_friend():
    """删除好友：{friend_id} → 双向删除好友记录"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        friend_id = data.get('friend_id')
        if not friend_id:
            return resp_json(PARAMS_ERROR, "缺少 friend_id", data=None)
        friend_id = int(friend_id)
        me = g.user.id
        if not _are_friends(me, friend_id):
            return resp_json(PARAMS_ERROR, "对方不在你的好友列表中", data=None)
        db.session.execute(friends.delete().where(
            (friends.c.user_id == me) & (friends.c.friend_id == friend_id)))
        db.session.execute(friends.delete().where(
            (friends.c.user_id == friend_id) & (friends.c.friend_id == me)))
        db.session.commit()
        return resp_json(OK, "已删除好友", data={"friend_id": friend_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


# --------------------------------------------------------------------------
# 好友备注：仅作用于“我”这一侧（单向），设置后当前用户看到该人一律显示备注名
@bp.route('/remark', methods=['POST'])
@login_required
def set_remark():
    """设置/清除好友备注：{friend_id, remark}；remark 传空串表示清除"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        friend_id = data.get('friend_id')
        if not friend_id:
            return resp_json(PARAMS_ERROR, "缺少 friend_id", data=None)
        friend_id = int(friend_id)
        me = g.user.id
        remark = (data.get('remark') or '').strip()[:64] or None
        row = db.session.query(friends.c.friend_id).filter(
            friends.c.user_id == me, friends.c.friend_id == friend_id).first()
        if not row:
            return resp_json(PARAMS_ERROR, "对方不在你的好友列表中", data=None)
        db.session.execute(
            friends.update()
            .where((friends.c.user_id == me) & (friends.c.friend_id == friend_id))
            .values(remark=remark)
        )
        db.session.commit()
        return resp_json(OK, "备注已更新" if remark else "已清除备注",
                         data={"friend_id": friend_id, "remark": remark})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/remarks', methods=['GET'])
@login_required
def list_remarks():
    """我设置过备注的好友映射（供前端本地覆盖显示名）"""
    try:
        me = g.user.id
        rows = db.session.query(friends.c.friend_id, friends.c.remark).filter(
            friends.c.user_id == me, friends.c.remark.isnot(None)).all()
        return resp_json(OK, "获取成功", data={
            "items": [{"friend_id": r.friend_id, "remark": r.remark} for r in rows]
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


# --------------------------------------------------------------------------
# 黑名单：支持拉黑 用户 / 群聊 / 频道（统一 block_type + target_id）
_BLOCK_TYPES = ('user', 'group', 'channel')


def _blocked_ids(me, block_type):
    """返回我拉黑的某类型 target_id 集合（供会话列表/搜索/动态过滤）"""
    return {r.target_id for r in db.session.query(Blacklist.target_id).filter(
        Blacklist.user_id == me, Blacklist.block_type == block_type).all()}


def _who_blocked_me(me, block_type='user'):
    """返回把我拉黑了的该类型 target_id 集合（对方拉黑我 → 我找不到对方/收不到）"""
    return {r.user_id for r in db.session.query(Blacklist.user_id).filter(
        Blacklist.target_id == me, Blacklist.block_type == block_type).all()}


@bp.route('/blacklist/add', methods=['POST'])
@login_required
def blacklist_add():
    """拉黑：{block_type: user|group|channel, target_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        block_type = data.get('block_type') or 'user'
        target_id = data.get('target_id')
        if block_type not in _BLOCK_TYPES:
            return resp_json(PARAMS_ERROR, "block_type 仅支持 user/group/channel", data=None)
        if not target_id:
            return resp_json(PARAMS_ERROR, "缺少 target_id", data=None)
        target_id = int(target_id)
        me = g.user.id
        if block_type == 'user':
            if target_id == me:
                return resp_json(PARAMS_ERROR, "不能拉黑自己", data=None)
            if User.query.filter_by(id=target_id, status=1).first() is None:
                return resp_json(PARAMS_ERROR, "目标用户不存在", data=None)
        elif block_type == 'group':
            from models.group import Group
            if Group.query.filter_by(id=target_id, status=1).first() is None:
                return resp_json(PARAMS_ERROR, "群聊不存在或已解散", data=None)
        else:
            from models.channel import Channel
            if Channel.query.filter_by(id=target_id, status=1).first() is None:
                return resp_json(PARAMS_ERROR, "频道不存在", data=None)
        exists = Blacklist.query.filter_by(user_id=me, block_type=block_type, target_id=target_id).first()
        if not exists:
            db.session.add(Blacklist(user_id=me, block_type=block_type, target_id=target_id))
            db.session.commit()
        return resp_json(OK, "已拉黑", data={"block_type": block_type, "target_id": target_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/blacklist/remove', methods=['POST'])
@login_required
def blacklist_remove():
    """取消拉黑：{block_type, target_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        block_type = data.get('block_type') or 'user'
        target_id = data.get('target_id')
        if block_type not in _BLOCK_TYPES or not target_id:
            return resp_json(PARAMS_ERROR, "参数不合法", data=None)
        target_id = int(target_id)
        row = Blacklist.query.filter_by(user_id=g.user.id, block_type=block_type, target_id=target_id).first()
        if row:
            db.session.delete(row)
            db.session.commit()
        return resp_json(OK, "已取消拉黑", data={"block_type": block_type, "target_id": target_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/blacklist/list', methods=['GET'])
@login_required
def blacklist_list():
    """我的黑名单：返回带展示信息（名称/头像/编号）的条目，按拉黑时间倒序"""
    try:
        from models.group import Group
        from models.channel import Channel
        me = g.user.id
        rows = Blacklist.query.filter_by(user_id=me).order_by(Blacklist.created_at.desc()).all()
        uids = [r.target_id for r in rows if r.block_type == 'user']
        gids = [r.target_id for r in rows if r.block_type == 'group']
        cids = [r.target_id for r in rows if r.block_type == 'channel']
        umap = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}
        gmap = {grp.id: grp for grp in Group.query.filter(Group.id.in_(gids)).all()} if gids else {}
        cmap = {chn.id: chn for chn in Channel.query.filter(Channel.id.in_(cids)).all()} if cids else {}
        result = []
        for r in rows:
            name = avatar = number = None
            if r.block_type == 'user':
                u = umap.get(r.target_id)
                if u: name, avatar, number = u.nickname, u.avatar, u.number
            elif r.block_type == 'group':
                grp = gmap.get(r.target_id)
                if grp: name, avatar, number = grp.name, grp.avatar, grp.number
            else:
                chn = cmap.get(r.target_id)
                if chn: name, avatar, number = chn.name, chn.avatar, chn.number
            if name is None:
                continue
            result.append({
                "block_type": r.block_type, "target_id": r.target_id,
                "name": name, "avatar": avatar, "number": number,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return resp_json(OK, "获取成功", data={"items": result, "total": len(result)})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)
