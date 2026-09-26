from flask_socketio import join_room, emit
from exts import socketio,db
from decorators import login_required
from flask import g,jsonify,request
from models.message import Message,Conversation

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
@login_required
def connect():
    try:
        print("客户端建立连接")
        owner_room = g.user.id
        join_room(owner_room)
    except Exception as e:
        warn = f"出现错误,{str(e)}"
        print(warn)

@socketio.on("disconnect")
@login_required
def disconnect():
    try:
        print("客户端断开连接")
    except Exception as e:
        warn = f"出现错误,{str(e)}"
        print(warn)

@socketio.on("send_private_msg")
@login_required
def send_private_msg(data):
    try:
        if data is None:
            return {'code':400,'msg':'数据体为空'}
        from_user = g.user.id
        to_user_id = data["to_user_id"]
        if not to_user_id:
            return {'code':400,'msg':"缺少目标用户"}
        msg = data["msg"]
        if msg is None or msg == '':
            return {'code':400,'msg':'消息不能为空'}
        conv = get_or_create_conversation(from_user, to_user_id)
        message = Message(sender_id=from_user, receiver_id=to_user_id, content=msg)
        db.session.add(message)
        db.session.flush()
        conv.last_msg_id = message.id
        db.session.commit()
        emit("message", {"from_user": from_user, "msg": msg}, room=to_user_id)
        return {'code':200,'msg':'发送成功'}
    except Exception as e:
        db.session.rollback()
        return {'code': 500, 'msg': f'发送失败: {str(e)}'}





