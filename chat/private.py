from flask_socketio import join_room, emit
from exts import socketio,db
from decorators import login_required
from flask import g,jsonify,request
from datetime import datetime, timezone
from models.message import Message,Conversation
from models.user import User, UserOnlineLog
from models.contact import Blacklist
from chat import ensure_socket_user
from presence import mark_online, mark_offline
from utils import guard_sensitive, is_user_muted, touch_private_conversation, quote_preview

#------------------------------------------------------------------------------------------
def get_or_create_conversation(user1_id,user2_id):
    conv = Conversation.query.filter(
        ((Conversation.user1_id == user1_id) & (Conversation.user2_id == user2_id)) |
        ((Conversation.user1_id == user2_id) & (Conversation.user2_id == user1_id))
    ).first()
    if not conv:
        conv = Conversation(user1_id=user1_id, user2_id=user2_id)
        db.session.add(conv)
        db.session.commit()
    return conv
#------------------------------------------------------------------------------------------
@socketio.on("connect")
def connect(auth=None):
    try:
        user = ensure_socket_user()
        if user is None:
            # 未登录/被封禁/已注销，拒绝连接
            return False
        print(f"客户端建立连接: user_id={user.id}")
        join_room(str(user.id))
        # 记录上线：加入在线注册表并刷新 last_online（DB 失败不影响连接）
        try:
            became_online = mark_online(user.id)
            now = datetime.now(timezone.utc)
            if became_online:
                # 离线→在线：开启一条在线会话流水（供管理端统计平均在线时长）
                db.session.add(UserOnlineLog(user_id=user.id, login_at=now))
            user.last_online = now
            db.session.commit()
        except Exception:
            db.session.rollback()
    except Exception as e:
        warn = f"出现错误,{str(e)}"
        print(warn)
        return False

@socketio.on("disconnect")
def disconnect():
    try:
        print("客户端断开连接")
        # 完全离线（连接数归零）时把 last_online 记为此刻，并关闭在线会话流水
        user = ensure_socket_user()
        if user is not None and mark_offline(user.id):
            try:
                now = datetime.now(timezone.utc)
                user.last_online = now
                log = UserOnlineLog.query.filter_by(user_id=user.id, logout_at=None) \
                    .order_by(UserOnlineLog.id.desc()).first()
                if log:
                    login = log.login_at if log.login_at.tzinfo else log.login_at.replace(tzinfo=timezone.utc)
                    log.logout_at = now
                    log.duration_seconds = max(0, int((now - login).total_seconds()))
                db.session.commit()
            except Exception:
                db.session.rollback()
    except Exception as e:
        warn = f"出现错误,{str(e)}"
        print(warn)

@socketio.on("send_private_msg")
def send_private_msg(data):
    try:
        user = ensure_socket_user()
        if user is None:
            return {'code':401,'msg':'未登录或账号异常'}
        if data is None:
            return {'code':400,'msg':'数据体为空'}
        from_user = user.id
        to_user_id = data.get("to_user_id")
        if not to_user_id:
            return {'code':400,'msg':"缺少目标用户"}
        # 硬性规则：不允许与自己私聊（会话列表/历史/文件接口均已拦截，这里封住发送源头）
        if int(to_user_id) == from_user:
            return {'code':400,'msg':'不能给自己发送消息'}
        msg = data.get("msg")
        if msg is None or msg == '':
            return {'code':400,'msg':'消息不能为空'}
        # 接收方必须存在且正常
        receiver = User.query.filter_by(id=to_user_id, status=1).first()
        if receiver is None or receiver.deleted_at is not None:
            return {'code':400,'msg':'接收用户不存在'}
        # 双向拉黑拦截：对方拉黑我 或 我拉黑对方，均拒发
        if Blacklist.query.filter_by(user_id=to_user_id, block_type='user', target_id=from_user).first():
            return {'code': 403, 'msg': '你已被对方拉黑，无法发送'}
        if Blacklist.query.filter_by(user_id=from_user, block_type='user', target_id=to_user_id).first():
            return {'code': 403, 'msg': '你已拉黑对方，请先取消拉黑'}
        # 全局禁言校验（敏感词违规累计触发）
        if is_user_muted(user):
            return {'code': 403, 'msg': '你已被禁言，暂时无法发送消息'}
        # 敏感词命中即拦截（不存库）
        ok, smsg = guard_sensitive(msg, from_user, 'private')
        if not ok:
            return {'code': 403, 'msg': smsg}
        conv = get_or_create_conversation(from_user, to_user_id)
        # 引用回复：reply_to 指向本会话内双方的一条消息，解析发送者昵称与摘要（非法/跨会话则忽略）
        reply_to = data.get("reply_to")
        try:
            reply_to = int(reply_to) if reply_to else None
        except (TypeError, ValueError):
            reply_to = None
        reply_nick = reply_prev = None
        if reply_to:
            ref = Message.query.get(reply_to)
            if ref and {ref.sender_id, ref.receiver_id} == {from_user, int(to_user_id)}:
                reply_nick = ref.sender.nickname if ref.sender else None
                reply_prev = quote_preview(ref.msg_type, ref.content, deleted=bool(ref.deleted_at))
            else:
                reply_to = None
        message = Message(sender_id=from_user, receiver_id=to_user_id, content=msg, reply_to=reply_to)
        db.session.add(message)
        db.session.flush()
        conv.last_msg_id = message.id
        # 刷新双方会话列表（接收方未读+1）
        touch_private_conversation(from_user, to_user_id, message.id, msg)
        db.session.commit()
        emit("message", {
            "from_user": from_user, "msg": msg, "msg_id": message.id, "msg_type": message.msg_type,
            "created_at": message.created_at.isoformat(),
            "reply_to": reply_to, "reply_to_nickname": reply_nick, "reply_to_content": reply_prev,
        }, room=str(to_user_id))
        return {'code': 200, 'msg': '发送成功', 'msg_id': message.id, 'created_at': message.created_at.isoformat(),
                'reply_to': reply_to, 'reply_to_nickname': reply_nick, 'reply_to_content': reply_prev}
    except Exception as e:
        db.session.rollback()
        return {'code': 500, 'msg': f'发送失败: {str(e)}'}





