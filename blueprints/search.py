from flask import render_template,Blueprint,g,request,jsonify,redirect,url_for
from decorators import login_required
from exts import db
from models.user import User
from models.group import Group, GroupMember, GroupMessage, GroupJoinRequest
from models.channel import Channel, ChannelMember, ChannelPost
from models.message import Message, MessageDelete
from models.upload import ConversationFile, GroupFile
from models.contact import friends, Blacklist
bp = Blueprint('search',__name__,url_prefix='/search')

@bp.route('/number',methods=['GET','POST'])
@login_required
# 输入用户/群聊/频道的number返回id,供聊天室room用或者添加用户为好友/加入群聊/订阅频道
def search():
    try:
        if request.method == 'GET':
            return render_template('number_search.html')
        data = request.get_json()
        if data is None:
            return jsonify({"code": 400, "msg": "需要JSON格式的信息", "data": None}), 400
        search_number = data.get('search_number')
        if search_number is None:
            return jsonify({"code":400,"msg":"请输入要搜索的代号","data": None}), 400
        if search_number.startswith("user_"):
            user = User.query.filter(User.number==search_number).first()
            if user is None:
                return jsonify({"code":400,"msg":"该用户不存在","data": None}), 400
            # 拉黑双向：对方拉黑了我 或 我拉黑了对方 → 对彼此“查不到”
            me_id = g.user.id
            if Blacklist.query.filter_by(user_id=user.id, block_type='user', target_id=me_id).first() or \
               Blacklist.query.filter_by(user_id=me_id, block_type='user', target_id=user.id).first():
                return jsonify({"code":400,"msg":"该用户不存在","data": None}), 400
            to_user_id = user.id
            return jsonify({
                "code":0,
                "msg":"查询用户成功",
                "data":{
                    "to_user_id":to_user_id,
                    "nickname":user.nickname,
                    "avatar":user.avatar
                }
            })
        elif search_number.startswith("group_"):
            group = Group.query.filter(Group.number==search_number).first()
            if group is None:
                return jsonify({"code":400,"msg":"该组不存在","data": None}), 400
            to_group_id = group.id
            return jsonify({
                "code":0,
                "msg":"查询群聊成功",
                "data":{
                    "to_group_id":to_group_id,
                    "name":group.name,
                    "avatar":group.avatar
                }
            })
        elif search_number.startswith("channel_"):
            channel = Channel.query.filter(Channel.number==search_number).first()
            if channel is None:
                return jsonify({"code": 400, "msg": "该频道不存在", "data": None}), 400
            to_channel_id = channel.id
            return jsonify({
                    "code": 0,
                    "msg": "查询频道成功",
                    "data": {
                     "to_channel_id": to_channel_id,
                     "name": channel.name,
                     "avatar": channel.avatar
                    }
                    })
        else:
            return jsonify({
                "code":400,
                "msg":"输入无效前缀名",
                "data":None
            }),400
    except Exception as e:
        return jsonify({
            "code":500,
            "msg":f"后端服务器错误{str(e)}",
            "data":None
        }),500


# 关键词发现搜索：按昵称/名称/简介或编号检索“尚未添加”的用户/群聊/频道（供“添加·搜索”弹窗使用）
@bp.route('/keyword', methods=['POST'])
@login_required
def search_keyword():
    """入参 {keyword, type}，type: all|user|group|channel（默认 all）。
    - 用户：仅正常未注销，匹配编号或昵称，只回昵称/头像/编号（不暴露邮箱）
    - 群聊：仅未解散，匹配编号或名称/简介
    - 频道：仅未封禁且 is_public=1，匹配编号或名称/简介
    每类最多 20 条；附带当前用户是否已加入/已订阅/已是好友。"""
    try:
        data = request.get_json(silent=True) or {}
        kw = (data.get('keyword') or "").strip()
        stype = data.get('type') or 'all'
        if stype not in ('all', 'user', 'group', 'channel'):
            stype = 'all'
        if not kw:
            return jsonify({"code": 400, "msg": "请输入搜索关键词", "data": None}), 400
        # 转义 LIKE 通配符，% _ 当作普通字符
        like = "%" + kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        uid = g.user.id
        LIMIT = 20
        out = {"users": [], "groups": [], "channels": []}

        if stype in ('all', 'user'):
            rows = User.query.filter(
                User.status == 1, User.deleted_at.is_(None),
                db.or_(User.number == kw, User.number == 'user_' + kw, User.nickname.like(like))
            ).limit(LIMIT).all()
            # 拉黑双向排除：我拉黑的 + 拉黑我的，都不出现在搜索结果
            bl_out = {r.target_id for r in Blacklist.query.filter_by(user_id=uid, block_type='user').all()}
            bl_in = {r.user_id for r in Blacklist.query.filter_by(target_id=uid, block_type='user').all()}
            for u in rows:
                if u.id == uid or u.id in bl_out or u.id in bl_in:
                    continue
                is_friend = db.session.query(friends.c.user_id).filter(
                    friends.c.user_id == uid, friends.c.friend_id == u.id).first() is not None
                out["users"].append({
                    "user_id": u.id, "nickname": u.nickname,
                    "avatar": u.avatar, "number": u.number, "is_friend": is_friend,
                })

        if stype in ('all', 'group'):
            rows = Group.query.filter(
                Group.status == 1,
                db.or_(Group.number == kw, Group.number == 'group_' + kw,
                       Group.name.like(like), Group.description.like(like))
            ).limit(LIMIT).all()
            for gr in rows:
                joined = GroupMember.query.filter_by(group_id=gr.id, user_id=uid).first() is not None
                pending = GroupJoinRequest.query.filter_by(group_id=gr.id, user_id=uid, status=0).first() is not None
                cnt = GroupMember.query.filter_by(group_id=gr.id).count()
                out["groups"].append({
                    "group_id": gr.id, "name": gr.name, "avatar": gr.avatar,
                    "number": gr.number, "member_count": cnt, "joined": joined, "pending": pending,
                })

        if stype in ('all', 'channel'):
            rows = Channel.query.filter(
                Channel.status == 1, Channel.is_public == 1,
                db.or_(Channel.number == kw, Channel.number == 'channel_' + kw,
                       Channel.name.like(like), Channel.description.like(like))
            ).limit(LIMIT).all()
            for ch in rows:
                sub = ChannelMember.query.filter_by(channel_id=ch.id, user_id=uid).first() is not None
                cnt = ChannelMember.query.filter_by(channel_id=ch.id).count()
                out["channels"].append({
                    "channel_id": ch.id, "name": ch.name, "avatar": ch.avatar,
                    "number": ch.number, "subscriber_count": cnt, "joined": sub,
                })

        total = len(out["users"]) + len(out["groups"]) + len(out["channels"])
        return jsonify({"code": 0, "msg": "搜索成功",
                        "data": {"keyword": kw, "type": stype, "results": out, "total": total}})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"后端服务器错误{str(e)}", "data": None}), 500


#搜索内容【基于用户加入的频道、群聊、私聊消息】
@bp.route('/content',methods=['GET','POST'])
@login_required
def search_content():
    """
    内容搜索：只在当前用户相关的会话内检索关键字
    - 私聊：只搜索自己发送或接收的消息
    - 群聊：只搜索自己已加入的群的消息
    - 频道：只搜索自己已订阅频道的帖子（标题+正文）
    data = {"keyword": "关键字", "scope": ["private", "group", "channel"], "limit": 50}
    scope 不传默认三类全搜，limit 单类上限默认50、最大200
    """
    try:
        if request.method == 'GET':
            return render_template('content_search.html')
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({"code":400,"msg":"需要JSON格式的信息","data":None}), 400
        keyword = (data.get('keyword') or "").strip()
        if not keyword:
            return jsonify({"code":400,"msg":"请输入要搜索的内容","data":None}), 400
        scope = data.get('scope') or ["private", "group", "channel"]
        if not isinstance(scope, list) or not set(scope) <= {"private", "group", "channel"}:
            return jsonify({"code":400,"msg":"scope仅支持private/group/channel","data":None}), 400
        # conv：可选，限定在单个会话内搜索（右上角会话内搜索用），形如 {"type": "private|group|channel", "id": int}
        conv = data.get('conv')
        conv_type = None
        conv_id = 0
        if conv:
            if not isinstance(conv, dict) or conv.get('type') not in ("private", "group", "channel"):
                return jsonify({"code":400,"msg":"conv需为{type,id}","data":None}), 400
            conv_type = conv.get('type')
            try:
                conv_id = int(conv.get('id') or 0)
            except (TypeError, ValueError):
                conv_id = 0
            if conv_id <= 0:
                return jsonify({"code":400,"msg":"conv.id无效","data":None}), 400
        try:
            limit = int(data.get('limit', 50))
        except (TypeError, ValueError):
            return jsonify({"code":400,"msg":"limit必须为整数","data":None}), 400
        limit = min(max(limit, 1), 200)
        # 转义LIKE通配符，把 % _ 当作普通字符搜索
        like_pattern = "%" + keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        user_id = g.user.id
        results = {"private": [], "group": [], "channel": []}

        # 私聊消息：自己发送或接收的，且未被全局删除、未被当前用户删除
        # 只匹配“文本内容”与“文件消息的原始文件名”（文件名存 ConversationFile，按 URL 关联）；
        # 图片/相册/转发(msg_type 2/4/5)的 content 是 URL/JSON，不参与匹配，避免搜出原始链接
        if not conv_type or conv_type == "private":
            deleted_for_me = db.session.query(MessageDelete.message_id).filter(MessageDelete.user_id == user_id)
            pair = db.or_(Message.sender_id == user_id, Message.receiver_id == user_id)
            if conv_type == "private":  # 只搜与某个对方的会话
                pair = db.or_(db.and_(Message.sender_id == user_id, Message.receiver_id == conv_id),
                              db.and_(Message.sender_id == conv_id, Message.receiver_id == user_id))
            text_cond = db.or_(Message.msg_type.is_(None), Message.msg_type == 1)
            rows = (
                db.session.query(Message, User.nickname, ConversationFile.filename)
                .join(User, User.id == Message.sender_id)
                .outerjoin(ConversationFile, ConversationFile.file_url == Message.content)
                .filter(pair,
                        Message.deleted_at.is_(None),
                        Message.id.notin_(deleted_for_me),
                        db.or_(
                            db.and_(text_cond, Message.content.like(like_pattern)),
                            db.and_(Message.msg_type == 3, ConversationFile.filename.like(like_pattern)),
                        ))
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )
            for msg, nickname, fname in rows:
                display = (("[文件] " + fname) if fname else "[文件]") if msg.msg_type == 3 else (msg.content or "")
                results["private"].append({
                    "message_id": msg.id,
                    "msg_type": msg.msg_type or 1,
                    "content": msg.content,
                    "display": display,
                    "sender_id": msg.sender_id,
                    "sender_nickname": nickname,
                    "counterpart_id": msg.receiver_id if msg.sender_id == user_id else msg.sender_id,
                    "created_at": msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
                })

        # 群聊消息：限定自己已加入的群
        if not conv_type or conv_type == "group":
            joined_group_ids = db.session.query(GroupMember.group_id).filter(GroupMember.user_id == user_id)
            group_filter = GroupMessage.group_id.in_(joined_group_ids)
            if conv_type == "group":
                is_member = db.session.query(GroupMember.group_id).filter(
                    GroupMember.group_id == conv_id, GroupMember.user_id == user_id).first()
                if not is_member:
                    return jsonify({"code": 403, "msg": "你不在该群中", "data": None}), 403
                group_filter = GroupMessage.group_id == conv_id
            text_cond = db.or_(GroupMessage.msg_type.is_(None), GroupMessage.msg_type == 1)
            rows = (
                db.session.query(GroupMessage, User.nickname, Group.name, GroupFile.filename)
                .join(Group, Group.id == GroupMessage.group_id)
                .join(User, User.id == GroupMessage.sender_id)
                .outerjoin(GroupFile, GroupFile.file_url == GroupMessage.content)
                .filter(group_filter,
                        GroupMessage.deleted_at.is_(None),
                        db.or_(
                            db.and_(text_cond, GroupMessage.content.like(like_pattern)),
                            db.and_(GroupMessage.msg_type == 3, GroupFile.filename.like(like_pattern)),
                        ))
                .order_by(GroupMessage.created_at.desc())
                .limit(limit)
                .all()
            )
            for msg, nickname, group_name, fname in rows:
                display = (("[文件] " + fname) if fname else "[文件]") if msg.msg_type == 3 else (msg.content or "")
                results["group"].append({
                    "message_id": msg.id,
                    "msg_type": msg.msg_type or 1,
                    "group_id": msg.group_id,
                    "group_name": group_name,
                    "content": msg.content,
                    "display": display,
                    "sender_id": msg.sender_id,
                    "sender_nickname": nickname,
                    "created_at": msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
                })

        # 频道帖子：限定自己已订阅的频道，匹配标题或正文
        if not conv_type or conv_type == "channel":
            joined_channel_ids = db.session.query(ChannelMember.channel_id).filter(ChannelMember.user_id == user_id)
            channel_filter = ChannelPost.channel_id.in_(joined_channel_ids)
            if conv_type == "channel":
                is_sub = db.session.query(ChannelMember.channel_id).filter(
                    ChannelMember.channel_id == conv_id, ChannelMember.user_id == user_id).first()
                if not is_sub:
                    return jsonify({"code": 403, "msg": "你未订阅该频道", "data": None}), 403
                channel_filter = ChannelPost.channel_id == conv_id
            rows = (
                db.session.query(ChannelPost, Channel.name)
                .join(Channel, Channel.id == ChannelPost.channel_id)
                .filter(channel_filter,
                        ChannelPost.status == 1,
                        db.or_(ChannelPost.title.like(like_pattern), ChannelPost.content.like(like_pattern)))
                .order_by(ChannelPost.created_at.desc())
                .limit(limit)
                .all()
            )
            for post, channel_name in rows:
                results["channel"].append({
                    "post_id": post.id,
                    "channel_id": post.channel_id,
                    "channel_name": channel_name,
                    "title": post.title,
                    "content": post.content,
                    "author_id": post.author_id,
                    "created_at": post.created_at.strftime('%Y-%m-%d %H:%M:%S')
                })

        return jsonify({
            "code":0,
            "msg":"搜索成功",
            "data":{
                "keyword":keyword,
                "results":results,
                "counts":{k: len(v) for k, v in results.items()}
            }
        })
    except Exception as e:
        return jsonify({
            "code":500,
            "msg":f"后端服务器错误{str(e)}",
            "data":None
        }),500