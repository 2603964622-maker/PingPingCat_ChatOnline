from flask import render_template,Blueprint,request,jsonify,g
from sqlalchemy import func
from decorators import login_required
from models import User
from models.message import Message,Conversation,MessageDelete,UserConversation
from models.group import Group, GroupMessage, GroupMember
from models.channel import Channel, ChannelMember
from models.upload import ConversationFile, GroupFile
from models.contact import Blacklist
from exts import db, socketio
from utils import resp_json, save_upload_file, get_file_size, touch_private_conversation, touch_group_conversation, delete_old_file, quote_preview
from presence import describe as presence_describe
import datetime
import json

bp = Blueprint('chat',__name__,url_prefix='/chat')


#------------------------------------------------------------------------------------------
# 私聊会话摘要与删除清理辅助
# 硬性规则：撤回＝双方全量硬删除（消息+文件/图片/视频记录+磁盘）；删除＝单向隐藏，
# 仅当双方都执行过删除时才物理清除。两种情况都必须回写 user_conversations 摘要，
# 否则会话列表会残留已消失消息的 [图片]/[文件] 摘要。
def _summarize_private_message(m):
    """生成会话列表摘要：文本→原文，图片→[图片]，文件→[文件] 文件名"""
    if m.msg_type == 2:
        return "[图片]"
    if m.msg_type == 3:
        cf = ConversationFile.query.filter_by(file_url=m.content).first()
        return f"[文件] {cf.filename}" if cf else "[文件]"
    return m.content or ""


def _refresh_private_conversation(user_id, peer_id):
    """按 user_id 视角，把与 peer_id 的会话摘要重算为其仍可见的最后一条消息；无可见消息则清空"""
    conv = UserConversation.query.filter_by(
        user_id=user_id, conv_type='private', target_id=peer_id
    ).first()
    if not conv:
        return
    deleted_for_user = db.session.query(MessageDelete.message_id).filter(MessageDelete.user_id == user_id)
    last = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == user_id, Message.receiver_id == peer_id),
            db.and_(Message.sender_id == peer_id, Message.receiver_id == user_id)
        ),
        Message.deleted_at.is_(None),
        Message.id.notin_(deleted_for_user)
    ).order_by(Message.id.desc()).first()
    if last:
        conv.last_msg_id = last.id
        conv.last_content = (_summarize_private_message(last) or "")[:255] or None
    else:
        conv.last_msg_id = None
        conv.last_content = None


def _purge_private_message(message):
    """物理清除一条私聊消息：连带 ConversationFile 记录、双方 MessageDelete 标记一并删除。
    仅从会话/ORM 删除，磁盘文件由调用方在 commit 之后再删。返回待删磁盘文件 url 列表。"""
    urls = [message.content] if message.msg_type in (2, 3) and message.content else []
    if urls:
        for cf in ConversationFile.query.filter(ConversationFile.file_url.in_(urls)).all():
            db.session.delete(cf)
    for md in MessageDelete.query.filter_by(message_id=message.id).all():
        db.session.delete(md)
    # message_deletes 外键引用 messages，且两者无 relationship，需先 flush 子表删除再删父表，否则违反外键约束
    db.session.flush()
    db.session.delete(message)
    return urls


def _purge_both_deleted(me_id, peer_id, message_ids):
    """在“我”刚单向删除的消息中，找出对方也已单向删除的（双方都删除）→ 物理清除消息+文件。
    返回 (被物理清除的 message_id 列表, 待删磁盘 url 列表)。"""
    if not message_ids:
        return [], []
    peer_deleted = db.session.query(MessageDelete.message_id).filter(MessageDelete.user_id == peer_id)
    to_purge = Message.query.filter(
        Message.id.in_(message_ids), Message.id.in_(peer_deleted)
    ).all()
    purged_ids, urls = [], []
    for m in to_purge:
        purged_ids.append(m.id)
        urls += _purge_private_message(m)
    if purged_ids:
        # 消息被物理删除，双方各自可见的“最后一条”都可能变化
        _refresh_private_conversation(me_id, peer_id)
        _refresh_private_conversation(peer_id, me_id)
    return purged_ids, urls


@bp.route('/to_user_id', methods=['GET'])
@login_required
def get_to_user_id_by_avatar():
    try:
        """
            这里超级不明白，不明白前端怎么传输的to_user_id【假设前端会传递吧】
            
        """
        data = request.get_json()
        to_user_id = int(data['to_user_id'])
        if not to_user_id:
            return jsonify({"code": 400, "msg": "缺少 user_id 参数", "data": None}), 400

        user = User.query.get(to_user_id)
        if not user:
            return jsonify({"code": 404, "msg": "该用户不存在", "data": None}), 404

        return jsonify({
            "code": 200,
            "msg": "获取成功",
            "data": {
                "to_user_id": user.id,
                "number": user.number,
                "nickname": user.nickname,
                "avatar": user.avatar
            }
        }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"服务器错误：{str(e)}",
            "data": None
        }), 500


#------------------------------------------------------------------------------------------
@bp.route('/messages', methods=['GET'])
@login_required
def get_private_messages():
    """
    获取当前用户与指定对象的私聊历史消息（分页）
    - 只返回 我↔对方 两个方向的消息
    - 自动排除：全局删除(deleted_at) + 当前用户已单向删除(MessageDelete)的消息
    - 对方删除的消息不影响本方查看（各自独立）
    查询参数：to_user_id(必填), before_id(可选，加载比该id更早的消息), per_page(默认20，最大100)
    """
    try:
        to_user_id = request.args.get('to_user_id', type=int)
        if not to_user_id:
            return resp_json(code=400, msg="缺少 to_user_id 参数", data=None)
        me_id = g.user.id
        if to_user_id == me_id:
            return resp_json(code=400, msg="不能查看与自己的聊天记录", data=None)

        peer = User.query.get(to_user_id)
        if not peer:
            return resp_json(code=404, msg="对方用户不存在", data=None)

        try:
            per_page = int(request.args.get('per_page', 20))
        except (TypeError, ValueError):
            per_page = 20
        per_page = min(max(per_page, 1), 100)
        before_id = request.args.get('before_id', type=int)

        # 当前用户已单向删除的消息id子查询
        deleted_for_me = db.session.query(MessageDelete.message_id).filter(MessageDelete.user_id == me_id)

        query = Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == me_id, Message.receiver_id == to_user_id),
                db.and_(Message.sender_id == to_user_id, Message.receiver_id == me_id)
            ),
            Message.deleted_at.is_(None),
            Message.id.notin_(deleted_for_me)
        )
        if before_id:
            query = query.filter(Message.id < before_id)

        messages = query.order_by(Message.id.desc()).limit(per_page).all()
        messages.reverse()  # 升序返回，前端直接追加

        msg_list = []
        # 文件消息(msg_type=3)/相册(msg_type=4)的 content 是文件 URL 或 JSON 数组，
        # 原始文件名/大小存在 ConversationFile 表里，需按 URL 批量关联补齐，否则前端只能从 URL 猜出 UUID 名
        urls_of = set()
        for m in messages:
            if m.msg_type == 3 and m.content:
                urls_of.add(m.content)
            elif m.msg_type == 4 and m.content:
                try:
                    for it in json.loads(m.content):
                        if it.get("url"): urls_of.add(it["url"])
                except Exception:
                    pass
        fmap = {}
        if urls_of:
            for f in ConversationFile.query.filter(ConversationFile.file_url.in_(list(urls_of))).all():
                fmap[f.file_url] = f
        # 引用回复：批量读取被引用消息（昵称+摘要），避免逐条查询
        rids = {m.reply_to for m in messages if m.reply_to}
        ref_map = {}
        if rids:
            for ref in Message.query.filter(Message.id.in_(list(rids))).all():
                ref_map[ref.id] = {
                    "nickname": ref.sender.nickname if ref.sender else None,
                    "preview": quote_preview(ref.msg_type, ref.content,
                                             cf.filename if (ref.msg_type == 3 and (cf := fmap.get(ref.content))) else None,
                                             deleted=bool(ref.deleted_at)),
                }
        for m in messages:
            cf = fmap.get(m.content) if m.msg_type == 3 else None
            ref = ref_map.get(m.reply_to) if m.reply_to else None
            msg_list.append({
                "msg_id": m.id,
                "sender_id": m.sender_id,
                "receiver_id": m.receiver_id,
                "is_self": m.sender_id == me_id,
                "content": m.content,
                "msg_type": m.msg_type,
                "filename": cf.filename if cf else None,
                "file_size": cf.file_size if cf else None,
                "reply_to": m.reply_to,
                "reply_to_nickname": ref["nickname"] if ref else None,
                "reply_to_content": ref["preview"] if ref else None,
                "created_at": m.created_at.isoformat() if m.created_at else None
            })

        peer_pres = presence_describe(peer)
        return resp_json(code=0, msg="获取成功", data={
            "to_user_id": to_user_id,
            "peer_nickname": peer.nickname,
            "peer_avatar": peer.avatar,
            "peer_online": peer_pres["online"],
            "peer_presence": peer_pres["presence_text"],
            "messages": msg_list,
            "has_more": len(messages) == per_page
        })
    except Exception as e:
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/conversations', methods=['GET'])
@login_required
def get_conversations():
    """
    获取当前用户的最近会话列表（私聊+群聊+频道聚合），按最近活跃时间倒序
    收到任何一类消息都会自动刷新对应会话置顶（见 utils.touch_*_conversation）
    """
    try:
        rows = UserConversation.query.filter_by(user_id=g.user.id).order_by(
            UserConversation.updated_at.desc()
        ).all()

        # 黑名单过滤：我拉黑的目标 + 拉黑了我的人（user 双向）均不在会话列表展示
        bl_user = {r.target_id for r in Blacklist.query.filter_by(user_id=g.user.id, block_type='user').all()}
        bl_group = {r.target_id for r in Blacklist.query.filter_by(user_id=g.user.id, block_type='group').all()}
        bl_channel = {r.target_id for r in Blacklist.query.filter_by(user_id=g.user.id, block_type='channel').all()}
        blocked_by = {r.user_id for r in Blacklist.query.filter_by(target_id=g.user.id, block_type='user').all()}

        # 按类型收集 target id，批量查询避免 N+1
        private_ids = [r.target_id for r in rows if r.conv_type == 'private']
        group_ids = [r.target_id for r in rows if r.conv_type == 'group']
        channel_ids = [r.target_id for r in rows if r.conv_type == 'channel']

        user_map = {u.id: u for u in User.query.filter(User.id.in_(private_ids)).all()} if private_ids else {}
        # 注意：推导式/循环变量绝不能用 g，会遮蔽 flask.g 导致整个函数把 g 当局部变量（UnboundLocalError）
        group_map = {grp.id: grp for grp in Group.query.filter(Group.id.in_(group_ids)).all()} if group_ids else {}
        channel_map = {c.id: c for c in Channel.query.filter(Channel.id.in_(channel_ids)).all()} if channel_ids else {}
        # 当前用户在各频道中的角色（1=主理人 2=副主理人 3=订阅者），用于前端决定是否展示发帖栏（后端发帖权限 role!=3）
        channel_role_map = {}
        # 各频道的通知级别（1=正常 2=仅@ 3=静音），静音会话不出声音且角标退化为灰点
        channel_notify_map = {}
        if channel_ids:
            for m in ChannelMember.query.filter_by(user_id=g.user.id).filter(ChannelMember.channel_id.in_(channel_ids)).all():
                channel_role_map[m.channel_id] = m.role
                channel_notify_map[m.channel_id] = m.notify_level or 1
        # 各频道订阅者总数（频道成员行数），用于聊天头部副标题展示
        channel_sub_count = {}
        if channel_ids:
            for cid, cnt in db.session.query(ChannelMember.channel_id, func.count(ChannelMember.id)).filter(
                ChannelMember.channel_id.in_(channel_ids)
            ).group_by(ChannelMember.channel_id).all():
                channel_sub_count[cid] = cnt

        result = []
        for r in rows:
            # 硬性规则：不允许与自己私聊，存量自聊会话不再展示
            if r.conv_type == 'private' and r.target_id == g.user.id:
                continue
            # 黑名单：拉黑/被拉黑均隐藏该会话（收不到新消息，列表也找不到）
            if r.conv_type == 'private' and (r.target_id in bl_user or r.target_id in blocked_by):
                continue
            if r.conv_type == 'group' and r.target_id in bl_group:
                continue
            if r.conv_type == 'channel' and r.target_id in bl_channel:
                continue
            name, avatar = None, None
            if r.conv_type == 'private':
                u = user_map.get(r.target_id)
                if u:
                    name, avatar = u.nickname, u.avatar
            elif r.conv_type == 'group':
                grp = group_map.get(r.target_id)
                if grp:
                    name, avatar = grp.name, grp.avatar
            elif r.conv_type == 'channel':
                c = channel_map.get(r.target_id)
                if c:
                    name, avatar = c.name, c.avatar
                # 已退订的频道不再展示（自愈：清理退订前遗留的会话记录，避免点进去显示错误的发帖按钮）
                if r.target_id not in channel_role_map:
                    continue
            # 目标已被删除则会话不展示
            if name is None:
                continue
            result.append({
                "conv_type": r.conv_type,
                "target_id": r.target_id,
                "name": name,
                "avatar": avatar,
                "last_msg_id": r.last_msg_id,
                "last_content": r.last_content,
                "unread_count": r.unread_count or 0,
                "role": channel_role_map.get(r.target_id) if r.conv_type == 'channel' else None,
                # 频道静音存于 ChannelMember.notify_level；私聊/群聊静音存于会话行自身
                "notify_level": (channel_notify_map.get(r.target_id) if r.conv_type == 'channel' else (r.notify_level or 1)) if r.conv_type in ('channel', 'private', 'group') else None,
                "subscriber_count": channel_sub_count.get(r.target_id) if r.conv_type == 'channel' else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None
            })

        return resp_json(code=0, msg="获取成功", data={"conversations": result, "total": len(result)})
    except Exception as e:
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/contacts', methods=['GET'])
@login_required
def get_contacts():
    """通讯录：好友 + 我加入的群聊 + 我订阅的频道"""
    try:
        from models.contact import friends
        from models.group import Group, GroupMember
        from models.channel import Channel, ChannelMember
        me = g.user.id

        frows = db.session.query(User, friends.c.remark) \
            .join(friends, friends.c.friend_id == User.id) \
            .filter(friends.c.user_id == me).all()
        friend_list = [{"user_id": u.id, "number": u.number, "name": rk or u.nickname,
                        "remark": rk, "nickname": u.nickname, "avatar": u.avatar}
                       for u, rk in frows]

        group_list = []
        for m in GroupMember.query.filter_by(user_id=me).all():
            gr = Group.query.get(m.group_id)
            if gr and gr.status == 1:
                group_list.append({"group_id": gr.id, "number": gr.number, "name": gr.name,
                                   "avatar": gr.avatar, "role": m.role})

        channel_list = []
        for m in ChannelMember.query.filter_by(user_id=me).all():
            ch = Channel.query.get(m.channel_id)
            if ch and ch.status == 1:
                channel_list.append({"channel_id": ch.id, "number": ch.number, "name": ch.name,
                                     "avatar": ch.avatar, "role": m.role})

        return resp_json(code=0, msg="获取成功", data={
            "friends": friend_list,
            "groups": group_list,
            "channels": channel_list
        })
    except Exception as e:
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/conversations/read', methods=['POST'])
@login_required
def mark_conversation_read():
    """将会话未读数清零（打开某个会话时调用）
    data = {"conv_type": "group", "target_id": 12}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求体不能为空", data=None)
        conv_type = data.get('conv_type')
        target_id = data.get('target_id')
        if not conv_type or not target_id:
            return resp_json(code=400, msg="缺少 conv_type 或 target_id", data=None)
        conv = UserConversation.query.filter_by(user_id=g.user.id, conv_type=conv_type, target_id=target_id).first()
        if not conv:
            return resp_json(code=404, msg="会话不存在", data=None)
        conv.unread_count = 0
        db.session.commit()
        return resp_json(code=0, msg="已读", data=None)
    except Exception as e:
        db.session.rollback()
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/conversation/delete', methods=['POST'])
@login_required
def delete_private_conversation():
    """
    从会话列表移除与某人的私聊会话（仅删除列表记录，不动任何消息）
    - 消息始终保留在 message 表中：不做软删除（不写 MessageDelete），也不物理删除
    - 下次收到新消息或重新打开该会话时，历史记录仍会完整加载
    - 对方记录完全不受影响
    data = {"to_user_id": 5}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求体不能为空", data=None)
        to_user_id = data.get('to_user_id')
        if not to_user_id:
            return resp_json(code=400, msg="缺少 to_user_id 参数", data=None)
        me_id = g.user.id
        if int(to_user_id) == me_id:
            return resp_json(code=400, msg="不能删除与自己的会话", data=None)

        # 仅删除我在会话列表里的记录，保留全部历史消息（下次打开会重新加载）
        conv = UserConversation.query.filter_by(
            user_id=me_id, conv_type='private', target_id=to_user_id
        ).first()
        if conv:
            db.session.delete(conv)
        db.session.commit()

        return resp_json(code=0, msg="会话已从列表移除", data={"to_user_id": int(to_user_id)})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/conversation/notify', methods=['POST'])
@login_required
def set_conversation_notify():
    """
    设置私聊/群聊会话的通知级别（静音 / 取消静音）
    - 仅影响当前用户这一侧：静音后来音静默、会话列表角标退化为灰点，对方无感知
    data = {"conv_type": "private"|"group", "target_id": 5, "notify_level": 3}  # 1=正常 3=静音
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求体不能为空", data=None)
        conv_type = data.get('conv_type') or 'private'
        target_id = data.get('target_id')
        level = data.get('notify_level')
        if conv_type not in ('private', 'group'):
            return resp_json(code=400, msg="该接口仅支持私聊/群聊会话静音", data=None)
        if not target_id:
            return resp_json(code=400, msg="缺少 target_id 参数", data=None)
        level = 3 if level == 3 else 1
        conv = UserConversation.query.filter_by(
            user_id=g.user.id, conv_type=conv_type, target_id=int(target_id)
        ).first()
        if not conv:
            return resp_json(code=404, msg="会话不存在", data=None)
        conv.notify_level = level
        db.session.commit()
        return resp_json(code=0, msg="已静音" if level == 3 else "已取消静音", data={"notify_level": level})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/conversation/history/clear', methods=['POST'])
@login_required
def clear_private_history():
    """
    清空与某人的全部私聊聊天记录（仅对当前用户单向清空，对方记录不受影响）
    - 复用单向删除模型：为我对本会话内所有仍可见的消息写入 MessageDelete 标记
    - 若某条消息对方此前也已单向删除 → 双方都删除，物理清除消息与文件
    - 清空后回写会话摘要（列表退化为“暂无消息”），会话本身仍保留在列表
    data = {"to_user_id": 5}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求体不能为空", data=None)
        to_user_id = data.get('to_user_id')
        if not to_user_id:
            return resp_json(code=400, msg="缺少 to_user_id 参数", data=None)
        me_id = g.user.id
        if int(to_user_id) == me_id:
            return resp_json(code=400, msg="不能清空与自己的聊天记录", data=None)

        # 我在此会话中仍可见的消息（未全局删除、未被我此前单向删除）
        deleted_for_me = db.session.query(MessageDelete.message_id).filter(MessageDelete.user_id == me_id)
        msgs = Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == me_id, Message.receiver_id == to_user_id),
                db.and_(Message.sender_id == to_user_id, Message.receiver_id == me_id)
            ),
            Message.deleted_at.is_(None),
            Message.id.notin_(deleted_for_me)
        ).all()
        ids = [m.id for m in msgs]
        for mid in ids:
            db.session.add(MessageDelete(message_id=mid, user_id=me_id))
        db.session.flush()

        # 双方都删除的消息 → 物理清除；否则仅回退我这一侧的会话摘要
        purged_ids, urls = _purge_both_deleted(me_id, int(to_user_id), ids)
        if not purged_ids:
            _refresh_private_conversation(me_id, int(to_user_id))
        db.session.commit()
        for u in urls:
            delete_old_file(u)

        return resp_json(code=0, msg="聊天记录已清空", data={"cleared": len(ids)})
    except Exception as e:
        db.session.rollback()
        print(f"清空私聊聊天记录失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/conversation/media', methods=['GET'])
@login_required
def get_conversation_media():
    """
    私聊共享媒体：返回当前用户与某人之间收发过的全部图片/视频/文件
    - 只含媒体（msg_type 2=图片 3=文件/视频），排除已全局删除与已被我单向删除的
    - 按时间升序（最新在最后），支持 before_id 向更早翻页（画廊向上加载）
    - 对象存储路径已按 /private/会话id/ 归类，此处只按消息时间聚合展示
    查询参数：to_user_id(必填), before_id(可选), per_page(默认80，最大200)
    """
    try:
        to_user_id = request.args.get('to_user_id', type=int)
        if not to_user_id:
            return resp_json(code=400, msg="缺少 to_user_id 参数", data=None)
        me_id = g.user.id
        if to_user_id == me_id:
            return resp_json(code=400, msg="不能查看与自己的媒体", data=None)
        try:
            per_page = int(request.args.get('per_page', 80))
        except (TypeError, ValueError):
            per_page = 80
        per_page = min(max(per_page, 1), 200)
        before_id = request.args.get('before_id', type=int)

        deleted_for_me = db.session.query(MessageDelete.message_id).filter(MessageDelete.user_id == me_id)
        query = Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == me_id, Message.receiver_id == to_user_id),
                db.and_(Message.sender_id == to_user_id, Message.receiver_id == me_id)
            ),
            Message.msg_type.in_([2, 3]),
            Message.deleted_at.is_(None),
            Message.id.notin_(deleted_for_me)
        )
        if before_id:
            query = query.filter(Message.id < before_id)
        rows = query.order_by(Message.id.desc()).limit(per_page).all()
        rows.reverse()  # 升序：最新在最后

        # 批量关联 ConversationFile 补齐原始文件名/类型/大小（按 URL）
        urls_of = [m.content for m in rows if m.content]
        fmap = {}
        if urls_of:
            for f in ConversationFile.query.filter(ConversationFile.file_url.in_(urls_of)).all():
                fmap[f.file_url] = f
        media = []
        for m in rows:
            cf = fmap.get(m.content)
            media.append({
                "msg_id": m.id,
                "file_url": m.content,
                "filename": cf.filename if cf else None,
                # 无关联记录时兜底：图片消息=3，其余按文件=4（前端再按扩展名细分视频/图片）
                "file_type": cf.file_type if cf else (3 if m.msg_type == 2 else 4),
                "file_size": cf.file_size if cf else None,
                "is_self": m.sender_id == me_id,
                "created_at": m.created_at.isoformat() if m.created_at else None
            })
        return resp_json(code=0, msg="获取成功", data={
            "media": media,
            "oldest_id": (rows[0].id if rows else None),
            "has_more": len(rows) == per_page
        })
    except Exception as e:
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/message/delete', methods=['POST'])
@login_required
def delete_message():
    """
    删除私聊消息（软删除）
    - 删除者本人只能删除自己的消息
    - 接收者可以删除对方发给自己的消息（不影响发送者的记录）
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
        message = Message.query.get(message_id)
        if not message:
            return resp_json(code=404, msg="消息不存在", data=None)
        
        # 验证权限：只能删除自己发送或接收的消息
        if message.sender_id != g.user.id and message.receiver_id != g.user.id:
            return resp_json(code=403, msg="无权删除该消息", data=None)

        # 单向删除：仅为当前用户写入 MessageDelete 标记（本次及以后登录都不再显示），对方仍可见
        me_id = g.user.id
        peer_id = message.receiver_id if message.sender_id == me_id else message.sender_id
        if not MessageDelete.query.filter_by(message_id=message.id, user_id=me_id).first():
            db.session.add(MessageDelete(message_id=message.id, user_id=me_id))
            db.session.flush()

        # 若对方此前也已单向删除该消息 → 双方都删除，物理清除消息与文件/图片/视频
        purged_ids, urls = _purge_both_deleted(me_id, peer_id, [message.id])
        if not purged_ids:
            # 仅我单方删除：消息仍在，但我这边会话列表摘要需回退（避免残留 [图片]）
            _refresh_private_conversation(me_id, peer_id)
        db.session.commit()

        if purged_ids:
            for u in urls:
                delete_old_file(u)
            # 消息对双方都彻底消失，通知两端移除气泡并刷新会话列表
            socketio.emit("message_revoked", {"message_id": int(message_id)}, room=str(me_id))
            socketio.emit("message_revoked", {"message_id": int(message_id)}, room=str(peer_id))

        return resp_json(code=0, msg="消息已删除", data={"purged": bool(purged_ids)})
        
    except Exception as e:
        db.session.rollback()
        print(f"删除消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/message/withdraw', methods=['POST'])
@login_required
def withdraw_message():
    """
    撤回私聊消息（硬删除：双方记录一并从库中移除，媒体文件连带清理）
    - 只能撤回自己发送的消息，限发送后 2 分钟内
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
        message = Message.query.get(message_id)
        if not message:
            return resp_json(code=404, msg="消息不存在", data=None)
        
        # 验证权限：只能撤回自己发送的消息
        if message.sender_id != g.user.id:
            return resp_json(code=403, msg="无权撤回该消息", data=None)

        # 撤回时限：仅允许发送后 2 分钟内撤回
        now = datetime.datetime.now(datetime.timezone.utc)
        created = message.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        if created is not None and (now - created).total_seconds() > 120:
            return resp_json(code=400, msg="超过 2 分钟，无法撤回", data=None)

        # 保存原内容供撤回后重新编辑（前端回填输入框用）
        original_content = message.content if message.msg_type == 1 else None
        sender_id = message.sender_id
        receiver_id = message.receiver_id

        # 撤回＝全量硬删除：消息本体 + 文件/图片/视频关联记录 + 双方单向删除标记，随后清磁盘文件
        urls = _purge_private_message(message)
        # 消息消失后，双方会话列表的“最后一条摘要”要回退（否则残留 [图片]/[文件]）
        _refresh_private_conversation(sender_id, receiver_id)
        _refresh_private_conversation(receiver_id, sender_id)
        db.session.commit()
        for u in urls:
            delete_old_file(u)

        # 广播移除事件：双方在线界面都删掉这条气泡并刷新会话列表（自己的房间也发，前端幂等）
        socketio.emit("message_revoked", {"message_id": int(message_id)}, room=str(receiver_id))
        socketio.emit("message_revoked", {"message_id": int(message_id)}, room=str(sender_id))

        return resp_json(code=0, msg="消息已撤回", data={
            "original_content": original_content,  # 文本消息回填输入框；媒体消息为 None
            "message_id": message_id
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"撤回消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/message/edit', methods=['POST'])
@login_required
def edit_message():
    """
    编辑撤回的消息或已撤回重新发送的消息
    - 需要 message_id 和新的 content
    - 只能编辑自己发送的消息
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
        message = Message.query.get(message_id)
        if not message:
            return resp_json(code=404, msg="消息不存在", data=None)
        
        # 验证权限：只能编辑自己发送的消息
        if message.sender_id != g.user.id:
            return resp_json(code=403, msg="无权编辑该消息", data=None)
        
        # 更新内容
        message.content = new_content
        
        # 如果是撤回后重新编辑，更新 updated_at 时间
        # 这里我们没有 updated_at 字段，如果有可以添加
        
        db.session.commit()
        
        return resp_json(code=0, msg="消息已编辑", data={
            "message_id": message_id,
            "content": message.content
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"编辑消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
def _get_or_create_conversation(user1_id, user2_id):
    """查找/创建两人之间的私聊会话（与 chat/private.py 逻辑一致）"""
    conv = Conversation.query.filter(
        ((Conversation.user1_id == user1_id) & (Conversation.user2_id == user2_id)) |
        ((Conversation.user1_id == user2_id) & (Conversation.user2_id == user1_id))
    ).first()
    if not conv:
        conv = Conversation(user1_id=user1_id, user2_id=user2_id)
        db.session.add(conv)
        db.session.flush()
    return conv


#------------------------------------------------------------------------------------------
@bp.route('/message/file', methods=['POST'])
@login_required
def send_private_file():
    """
    私聊发送文件/图片（multipart/form-data）
    - 保存实体文件，写入 ConversationFile 元数据
    - 同时生成一条 Message(msg_type: 2=图片 3=文件)，使其出现在聊天记录中（从而可被单向删除/搜索）
    - 刷新双方会话列表，并实时推送给接收方
    form-data: to_user_id, file
    """
    try:
        to_user_id = request.form.get('to_user_id', type=int)
        if not to_user_id:
            return resp_json(code=400, msg="缺少 to_user_id 参数", data=None)
        me_id = g.user.id
        if to_user_id == me_id:
            return resp_json(code=400, msg="不能给自己发送文件", data=None)
        receiver = User.query.filter_by(id=to_user_id, status=1).first()
        if receiver is None or receiver.deleted_at is not None:
            return resp_json(code=400, msg="接收用户不存在", data=None)
        if Blacklist.query.filter_by(user_id=to_user_id, block_type='user', target_id=me_id).first():
            return resp_json(code=403, msg="你已被对方拉黑，无法发送", data=None)
        if Blacklist.query.filter_by(user_id=me_id, block_type='user', target_id=to_user_id).first():
            return resp_json(code=403, msg="你已拉黑对方，请先取消拉黑", data=None)

        file = request.files.get('file')
        if not file or not file.filename:
            return resp_json(code=400, msg="未选择文件", data=None)

        file_size = get_file_size(file)
        if file_size is None:
            return resp_json(code=400, msg="文件超出大小限制", data=None)
        # 先确保会话存在，用会话 id 作为对象存储目录（私聊文件混放）
        conv = _get_or_create_conversation(me_id, to_user_id)
        result = save_upload_file(file, scope=('private', conv.id))
        if result is None:
            return resp_json(code=400, msg="文件格式不支持或保存失败", data=None)
        file_url, file_type, filename = result
        # save_upload_file file_type: 3=图片→消息msg_type 2；其余→消息msg_type 3(文件)
        msg_type = 2 if file_type == 3 else 3

        message = Message(sender_id=me_id, receiver_id=to_user_id, content=file_url, msg_type=msg_type)
        db.session.add(message)
        db.session.flush()
        conv.last_msg_id = message.id

        conv_file = ConversationFile(
            conversation_id=conv.id, filename=filename, file_url=file_url,
            file_type=file_type, file_size=file_size, sender_id=me_id
        )
        db.session.add(conv_file)

        preview = f"[图片]" if msg_type == 2 else f"[文件] {filename}"
        touch_private_conversation(me_id, to_user_id, message.id, preview)
        db.session.commit()

        # 实时推送给接收方（接收方 room 为 str(user_id)）
        socketio.emit("message", {
            "from_user": me_id, "msg_type": msg_type, "msg_id": message.id,
            "content": file_url, "filename": filename, "file_size": file_size
        }, room=str(to_user_id))

        return resp_json(code=0, msg="文件发送成功", data={
            "msg_id": message.id, "msg_type": msg_type, "file_url": file_url,
            "filename": filename, "file_size": file_size
        })
    except Exception as e:
        db.session.rollback()
        print(f"私聊发送文件失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/message/forward', methods=['POST'])
@login_required
def forward_message():
    """转发一条已有消息（文本/图片/文件/相册）到另一个私聊或群聊会话。
    直接复制源消息的 content 与 msg_type（文件复用同一 file_url，不重复上传），
    并为目标会话补齐文件记录，使原始文件名可正确显示。
    data = {source_type: 'private'|'group', message_id, target_type: 'private'|'group', target_id}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        source_type = data.get('source_type')
        message_id = data.get('message_id')
        target_type = data.get('target_type')
        target_id = data.get('target_id')
        if not (source_type and message_id and target_type and target_id):
            return resp_json(code=400, msg="缺少 source_type/message_id/target_type/target_id", data=None)
        if source_type not in ('private', 'group') or target_type not in ('private', 'group'):
            return resp_json(code=400, msg="source_type/target_type 仅支持 private 或 group", data=None)
        me_id = g.user.id
        target_id = int(target_id)

        # 1. 读取源消息并校验可见性
        if source_type == 'private':
            src = Message.query.get(message_id)
            if not src or src.deleted_at is not None or me_id not in (src.sender_id, src.receiver_id):
                return resp_json(code=400, msg="源消息不存在或无权访问", data=None)
        else:
            src = GroupMessage.query.get(message_id)
            if not src or src.deleted_at is not None:
                return resp_json(code=400, msg="源消息不存在或已删除", data=None)
            if not GroupMember.query.filter_by(group_id=src.group_id, user_id=me_id).first():
                return resp_json(code=403, msg="你不在源消息所在群，无法转发", data=None)
        content = src.content
        msg_type = src.msg_type or 1
        if msg_type == 5:
            return resp_json(code=400, msg="该消息不支持再次转发", data=None)

        # 2. 文件消息(msg_type=3)：跨私聊/群文件表定位原始元数据，供目标表补记录
        file_meta = None  # (filename, file_type, file_size)
        if msg_type == 3 and content:
            cf = ConversationFile.query.filter_by(file_url=content).first()
            if cf:
                file_meta = (cf.filename, cf.file_type, cf.file_size)
            else:
                gf = GroupFile.query.filter_by(file_url=content).first()
                if gf:
                    file_meta = (gf.filename, gf.file_type, gf.file_size)

        if msg_type == 2:
            preview = "[图片]"
        elif msg_type == 3:
            preview = ("[文件] " + file_meta[0]) if file_meta else "[文件]"
        elif msg_type == 4:
            preview = "[相册]"
        else:
            preview = (content or "")[:255]
        fname = file_meta[0] if (msg_type == 3 and file_meta) else None
        fsize = file_meta[2] if (msg_type == 3 and file_meta) else None

        # 3. 写入目标会话
        if target_type == 'private':
            if target_id == me_id:
                return resp_json(code=400, msg="不能转发给自己", data=None)
            receiver = User.query.filter_by(id=target_id, status=1).first()
            if receiver is None or receiver.deleted_at is not None:
                return resp_json(code=400, msg="接收用户不存在", data=None)
            if Blacklist.query.filter_by(user_id=target_id, block_type='user', target_id=me_id).first():
                return resp_json(code=403, msg="你已被对方拉黑，无法发送", data=None)
            if Blacklist.query.filter_by(user_id=me_id, block_type='user', target_id=target_id).first():
                return resp_json(code=403, msg="你已拉黑对方，请先取消拉黑", data=None)
            conv = _get_or_create_conversation(me_id, target_id)
            message = Message(sender_id=me_id, receiver_id=target_id, content=content, msg_type=msg_type)
            db.session.add(message)
            db.session.flush()
            conv.last_msg_id = message.id
            if msg_type == 3 and file_meta and not ConversationFile.query.filter_by(file_url=content).first():
                db.session.add(ConversationFile(conversation_id=conv.id, filename=file_meta[0], file_url=content,
                                                file_type=file_meta[1], file_size=file_meta[2], sender_id=me_id))
            touch_private_conversation(me_id, target_id, message.id, preview)
            db.session.commit()
            socketio.emit("message", {
                "from_user": me_id, "msg_type": msg_type, "msg_id": message.id, "content": content,
                "filename": fname, "file_size": fsize,
            }, room=str(target_id))
        else:  # group
            group = Group.query.filter_by(id=target_id, status=1).first()
            if not group:
                return resp_json(code=400, msg="群聊不存在或已解散", data=None)
            member = GroupMember.query.filter_by(group_id=target_id, user_id=me_id).first()
            if not member:
                return resp_json(code=403, msg="你不是群成员，无法转发到该群", data=None)
            now = datetime.datetime.utcnow()
            if member.mute_until and member.mute_until > now:
                return resp_json(code=403, msg="你已被禁言，暂时无法发言", data=None)
            if group.mute_all and member.role not in (1, 2):
                return resp_json(code=403, msg="群已开启全员禁言", data=None)
            message = GroupMessage(sender_id=me_id, group_id=target_id, content=content, msg_type=msg_type)
            db.session.add(message)
            db.session.flush()
            if msg_type == 3 and file_meta and not GroupFile.query.filter_by(file_url=content).first():
                db.session.add(GroupFile(group_id=target_id, file_url=content, filename=file_meta[0],
                                         file_type=file_meta[1], file_size=file_meta[2], sender_id=me_id))
            touch_group_conversation(target_id, message.id, preview, me_id)
            db.session.commit()
            socketio.emit("receive_message", {
                "id": message.id, "sender_id": me_id, "sender_name": g.user.nickname,
                "sender_avatar": g.user.avatar, "group_id": target_id,
                "content": content, "msg_type": msg_type, "created_at": message.created_at.isoformat(),
                "filename": fname, "file_size": fsize,
            }, room=f"group_{target_id}")

        return resp_json(code=0, msg="已转发", data={
            "msg_id": message.id, "msg_type": msg_type, "content": content,
            "filename": fname, "file_size": fsize,
            "created_at": message.created_at.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        print(f"转发消息失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)

