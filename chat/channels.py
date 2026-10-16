from flask_socketio import join_room, leave_room, emit
from exts import socketio, db
from flask import g
from models.channel import ChannelPost, ChannelComment, ChannelMember
from models.user import User
from chat import ensure_socket_user
from utils import guard_sensitive, is_user_muted, touch_channel_conversation, create_notification
from bots.engine import run_bots

#------------------------------------------------------------------------------------------
@socketio.on("join_channel")
def join_channel_event(data):
    """
    客户端请求加入频道房间
    data = {"channel_id": 10}
    """
    try:
        channel_id = data.get("channel_id")
        
        room_name = f"channel_{channel_id}"
        join_room(room_name)
        
        print(f"用户 {g.user.id} 加入了频道房间 {room_name}")
        
        return True
        
    except Exception as e:
        print(f"错误：{e}")
        return False


@socketio.on("leave_channel")
def leave_channel_event(data):
    """客户端离开频道房间"""
    channel_id = data.get("channel_id")
    room_name = f"channel_{channel_id}"
    leave_room(room_name)
    return True


@socketio.on("join_channel_post")
def join_channel_post_room(data):
    """
    客户端请求加入特定频道的帖子评论房间
    data = {"channel_id": 10, "post_id": 50}
    """
    try:
        channel_id = data.get("channel_id")
        post_id = data.get("post_id")
        
        # ❌ 不需要预先创建！
        # ✅ 动态创建 "channel_10_post_50" 房间
        
        room_name = f"channel_{channel_id}_post_{post_id}"
        join_room(room_name)
        
        print(f"用户 {g.user.id} 加入了帖子房间 {room_name}")
        
        return True
        
    except Exception as e:
        print(f"错误：{e}")
        return False


@socketio.on("leave_channel_post")
def leave_channel_post_room(data):
    """离开帖子评论房间"""
    channel_id = data.get("channel_id")
    post_id = data.get("post_id")
    room_name = f"channel_{channel_id}_post_{post_id}"
    leave_room(room_name)
    return True


@socketio.on("publish_channel_post")
def publish_channel_post(data):
    """
    发布频道帖子
    权限验证：只有非订阅者才能发布
    data = {"channel_id": 10, "content": "帖子内容", "files": [...]}
    """
    try:
        user = ensure_socket_user()
        if user is None:
            return {'code': 401, 'msg': '未登录或账号异常'}
        channel_id = data.get("channel_id")
        content = data.get("content")

        if not channel_id or not content:
            return {'code': 400, 'msg': '缺少必要参数'}

        # 权限：必须是频道内非订阅者(role!=3)
        member = ChannelMember.query.filter_by(user_id=user.id, channel_id=channel_id).first()
        if not member or member.role == 3:
            return {'code': 403, 'msg': '订阅者无权发布帖子'}

        # 全局禁言校验（敏感词违规累计触发）——仅通过 ack 返回提示，不再 emit('error') 以免前端弹两次
        if is_user_muted(user):
            return {'code': 403, 'msg': '你已被禁言，暂时无法发布'}
        # 敏感词命中即拦截（不存库）
        ok, smsg = guard_sensitive(content, user.id, 'channel')
        if not ok:
            return {'code': 403, 'msg': smsg}

        # Bot 前置规则（防刷屏/防广告/重复内容/夜间禁言），命中则拦截
        blocked, bmsg = run_bots('channel', channel_id, 'message', 'pre',
                                 {'sender_id': user.id, 'sender_name': user.nickname,
                                  'text': content, 'msg_type': 1})
        if blocked:
            return {'code': 403, 'msg': bmsg}

        # 这里的 room 是整个频道，不是帖子房间
        room_name = f"channel_{channel_id}"

        # 保存帖子到数据库（作者字段为 author_id）
        post = ChannelPost(
            channel_id=channel_id,
            author_id=user.id,
            content=content
        )
        db.session.add(post)
        db.session.flush()

        # 刷新全体频道成员会话列表（除发布者未读+1）
        touch_channel_conversation(channel_id, post.id, content, user.id)
        # 广播前必须提交，否则 socket 事件结束时事务回滚，帖子不落库
        db.session.commit()

        # 广播给整个频道的成员
        post_data = {
            "id": post.id,
            "author_id": user.id,
            "author_nickname": user.nickname,
            "channel_id": channel_id,
            "content": content,
            "created_at": post.created_at.isoformat()
        }
        emit("receive_post", post_data, room=room_name)

        return {'code': 200, 'msg': '帖子发布成功'}

    except Exception as e:
        db.session.rollback()
        print(f"频道帖子发布失败：{e}")
        return {'code': 500, 'msg': str(e)}


@socketio.on("send_channel_comment")
def send_channel_comment(data):
    """
    发布帖子评论
    权限验证：只有非订阅者才能发布
    data = {"channel_id": 10, "post_id": 50, "comment": "评论内容"}
    """
    try:
        user = ensure_socket_user()
        if user is None:
            return {'code': 401, 'msg': '未登录或账号异常'}
        channel_id = data.get("channel_id")
        post_id = data.get("post_id")
        comment_content = data.get("comment")
        reply_to = data.get("reply_to")

        if not all([channel_id, post_id, comment_content]):
            return {'code': 400, 'msg': '缺少必要参数'}

        # 权限：未加入频道（非订阅者）只能查看评论，不能发布评论
        member = ChannelMember.query.filter_by(user_id=user.id, channel_id=channel_id).first()
        if not member:
            return {'code': 403, 'msg': '你未加入该频道，无法发布评论'}

        # 全局禁言校验（敏感词违规累计触发）——仅通过 ack 返回提示，不再 emit('error') 以免前端弹两次
        if is_user_muted(user):
            return {'code': 403, 'msg': '你已被禁言，暂时无法评论'}
        # 敏感词命中即拦截（不存库）
        ok, smsg = guard_sensitive(comment_content, user.id, 'channel')
        if not ok:
            return {'code': 403, 'msg': smsg}

        # Bot 前置规则（防刷屏/防广告/重复内容/夜间禁言），命中则拦截
        blocked, bmsg = run_bots('channel', channel_id, 'message', 'pre',
                                 {'sender_id': user.id, 'sender_name': user.nickname,
                                  'text': comment_content, 'msg_type': 1})
        if blocked:
            return {'code': 403, 'msg': bmsg}

        # 这里的 room 就是点击帖子时指定的评论房间
        room_name = f"channel_{channel_id}_post_{post_id}"

        # 保存评论到数据库
        comment = ChannelComment(
            channel_id=channel_id,
            post_id=post_id,
            user_id=user.id,
            content=comment_content,
            reply_to=int(reply_to) if reply_to else None
        )
        db.session.add(comment)
        db.session.flush()
        # 通知帖子作者（不通知自己）
        post = ChannelPost.query.get(post_id)
        if post and post.author_id != user.id:
            create_notification(
                user_id=post.author_id, type="reply", title="你的帖子收到新评论",
                content=comment_content[:100], related_id=post_id, related_type="channel_post"
            )
        # 回复场景：取被回复评论昵称/内容，并通知被回复者（非自己、与帖子作者不同避免重复）
        reply_nick = None
        reply_content = None
        if comment.reply_to:
            tgt = ChannelComment.query.get(comment.reply_to)
            if tgt:
                tu = User.query.get(tgt.user_id)
                reply_nick = tu.nickname if tu else None
                reply_content = tgt.content
                if tgt.user_id != user.id and (not post or tgt.user_id != post.author_id):
                    create_notification(
                        user_id=tgt.user_id, type="reply", title="你的评论收到回复",
                        content=f"{user.nickname} 回复了你：{comment_content[:60]}",
                        related_id=post_id, related_type="channel_post"
                    )
        # 广播前必须提交，否则 socket 事件结束时事务回滚，评论不落库
        db.session.commit()

        # 只广播给该帖子的评论房间的人
        comment_data = {
            "id": comment.id,
            "user_id": user.id,
            "user_nickname": user.nickname,
            "user_avatar": user.avatar,
            "channel_id": channel_id,
            "post_id": post_id,
            "content": comment_content,
            "reply_to": comment.reply_to,
            "reply_to_nickname": reply_nick,
            "reply_to_content": reply_content,
            "created_at": comment.created_at.isoformat()
        }
        emit("receive_comment", comment_data, room=room_name)

        return {'code': 200, 'msg': '评论成功'}
        
    except Exception as e:
        db.session.rollback()
        print(f"频道评论发送失败：{e}")
        return {'code': 500, 'msg': str(e)}