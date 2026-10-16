from flask_socketio import join_room, leave_room, emit
from exts import socketio, db
from flask import g
import datetime
from models.group import Group, GroupMessage, GroupMember
from chat import ensure_socket_user
from utils import guard_sensitive, is_user_muted, touch_group_conversation, create_notification, quote_preview
from bots.engine import run_bots

#------------------------------------------------------------------------------------------
@socketio.on("join_group")
def join_group_event(data):
    """
    客户端请求加入群组房间
    data = {"group_id": 123}
    """
    try:
        group_id = data.get("group_id")
        
        # ❌ 不需要预先创建房间！
        # ✅ join_room() 会自动创建这个房间
        
        room_name = f"group_{group_id}"
        join_room(room_name)
        
        print(f"用户 {g.user.id} 加入了群组房间 {room_name}")
        
        return True
        
    except Exception as e:
        print(f"错误：{e}")
        return False


@socketio.on("leave_group")
def leave_group_event(data):
    """客户端离开群聊房间"""
    group_id = data.get("group_id")
    room_name = f"group_{group_id}"
    leave_room(room_name)
    return True


@socketio.on("send_group_msg")
def send_group_msg(data):
    """发送群消息"""
    try:
        user = ensure_socket_user()
        if user is None:
            return {'code': 401, 'msg': '未登录或账号异常'}
        group_id = data.get("group_id")
        msg_content = data.get("msg")

        if not group_id or not msg_content:
            return {'code': 400, 'msg': '缺少必要参数'}

        # 必须是群成员才能发言
        member = GroupMember.query.filter_by(user_id=user.id, group_id=group_id).first()
        if not member:
            return {'code': 403, 'msg': '你不是群成员，无法发言'}

        # 禁言校验：个人禁言到期前不可发言；全员禁言时普通成员不可发言（群主与管理员不受限）
        now = datetime.datetime.utcnow()
        if member.mute_until and member.mute_until > now:
            return {'code': 403, 'msg': '你已被禁言，暂时无法发言'}
        group_obj = Group.query.filter_by(id=group_id).first()
        if group_obj and group_obj.mute_all and member.role not in (1, 2):
            return {'code': 403, 'msg': '群已开启全员禁言'}

        # @成员：仅保留本群有效成员 id，排除自己，存为逗号分隔字符串
        raw_mentions = data.get("mentions") or []
        if not isinstance(raw_mentions, list):
            raw_mentions = []
        mention_ids = []
        for mid in raw_mentions:
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                continue
            if mid != user.id and mid not in mention_ids:
                mention_ids.append(mid)
        mentions_str = ",".join(str(x) for x in mention_ids)[:255] if mention_ids else None

        # 全局禁言校验（敏感词违规累计触发）——仅通过 ack 返回提示，不再 emit('error') 以免前端弹两次
        if is_user_muted(user):
            return {'code': 403, 'msg': '你已被禁言，暂时无法发言'}
        # 敏感词命中即拦截（不存库）
        ok, smsg = guard_sensitive(msg_content, user.id, 'group', group_id)
        if not ok:
            return {'code': 403, 'msg': smsg}

        # Bot 前置规则（防刷屏/防广告/防垃圾图/重复内容/夜间禁言），命中则拦截不存库
        bot_ctx = {'sender_id': user.id, 'sender_name': user.nickname,
                   'text': msg_content, 'msg_type': 1}
        blocked, bmsg = run_bots('group', group_id, 'message', 'pre', bot_ctx)
        if blocked:
            return {'code': 403, 'msg': bmsg}

        # 保存到数据库
        # 引用回复：reply_to 指向本群内另一条消息，解析发送者昵称与摘要（非法/跨群则忽略）
        reply_to = data.get("reply_to")
        try:
            reply_to = int(reply_to) if reply_to else None
        except (TypeError, ValueError):
            reply_to = None
        reply_nick = reply_prev = None
        if reply_to:
            ref = GroupMessage.query.get(reply_to)
            if ref and ref.group_id == int(group_id):
                reply_nick = ref.sender.nickname if ref.sender else None
                reply_prev = quote_preview(ref.msg_type, ref.content, deleted=bool(ref.deleted_at))
            else:
                reply_to = None
        message = GroupMessage(
            sender_id=user.id,
            group_id=group_id,
            content=msg_content,
            mentions=mentions_str,
            reply_to=reply_to
        )
        db.session.add(message)
        db.session.flush()

        # 为被@成员创建站内通知并实时推送
        if mention_ids:
            for mid in mention_ids:
                if GroupMember.query.filter_by(group_id=group_id, user_id=mid).first():
                    create_notification(mid, 'mention', title='有人@你',
                                        content=f"{user.nickname} 在群聊中@了你",
                                        related_id=group_id, related_type='group')
                    emit('notification', {
                        'type': 'mention', 'title': '有人@你',
                        'content': f"{user.nickname} 在群聊中@了你",
                        'related_id': group_id, 'related_type': 'group', 'group_id': group_id
                    }, room=str(mid))

        # 刷新全体群成员会话列表（除发送者未读+1）
        touch_group_conversation(group_id, message.id, msg_content, user.id)
        # 必须在广播前提交！socket 事件结束会 rollback，只 flush 不 commit 消息不会落库（前端会拿到一个不存在 id 的幽灵消息）
        db.session.commit()

        # 广播到整个群组房间
        room_name = f"group_{group_id}"
        message_data = {
            "id": message.id,
            "sender_id": user.id,
            "sender_name": user.nickname,
            "sender_avatar": user.avatar,
            "group_id": group_id,
            "content": msg_content,
            "msg_type": message.msg_type,
            "mentions": mention_ids,
            "reply_to": reply_to,
            "reply_to_nickname": reply_nick,
            "reply_to_content": reply_prev,
            "created_at": message.created_at.isoformat()
        }
        emit("receive_message", message_data, room=room_name)

        # Bot 后置规则（关键词自动回复）
        run_bots('group', group_id, 'message', 'post', bot_ctx)

        return {'code': 200, 'msg': '发送成功'}

    except Exception as e:
        db.session.rollback()
        print(f"群聊发送失败：{e}")
        return {'code': 500, 'msg': str(e)}