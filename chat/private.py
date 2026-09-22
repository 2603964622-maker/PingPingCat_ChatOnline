from flask_socketio import join_room

from exts import socketio
from decorators import login_required
from flask import g,session


@socketio.on("connect")
@login_required
def connect():
    try:
        print("客户端建立连接")
        owner_room = g.user.number
        join_room(f"user_{owner_room}")
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
    pass
