from flask import Blueprint,render_template,request,redirect,url_for,jsonify,g,current_app,session
from sqlalchemy import true, func
import os
import json
import datetime
from decorators import login_required
from models.channel import Channel,ChannelMember,ChannelPost,ChannelLike,ChannelComment,ChannelAnnouncement,ChannelAnnouncementUserIsRead,ChannelInvite
from models.upload import ChannelFile
from models.user import User
from models.message import UserConversation, Message, Conversation
from models.group import Group, GroupMessage, GroupMember
from models.contact import Blacklist
from utils import generate_unique_number, resp_json, save_upload_file,generate_filename,get_file_size,delete_old_file,guard_sensitive,is_user_muted,create_notification,touch_channel_conversation,upsert_conversation,touch_private_conversation,touch_group_conversation
from models.bot import BotTemplate, ChannelBot
from bots.engine import run_bots
from exts import db,socketio
# 导入参数
from utils import OK,PARAMS_ERROR,SERVER_ERROR
bp = Blueprint('channels',__name__,url_prefix='/channel')
#---------------------------------------------------------------------------
def check_channel_member_role(channel_id):
    user_id = g.user.id
    member_role = ChannelMember.query.filter_by(user_id=user_id, channel_id=channel_id).first()
    if not member_role or member_role.role == 3:
        return False
    return True
def check_channel_member_is_boss(channel_id):
    user_id = g.user.id
    member_role = ChannelMember.query.filter_by(user_id=user_id, channel_id=channel_id).first()
    # 频道主 role==1
    if member_role and member_role.role == 1:
        return True
    return False
#---------------------------------------------------------------------------
@bp.route('/create', methods=['POST'])
@login_required
def create_channel():
    """
    创建频道,获取前端的数据,头像默认用 robohash，创建后自动把创建者添加到频道成员表中
    :param:request.get_json(data)
    :return: 成功：频道编号、创建者编号、频道号、频道名称、频道描述、频道头像、是否公开、状态
    :return: 失败：服务器内部错误 or 频道名称为空 or 前端传递信息错误
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求格式错误，需要JSON", "data": None}), 400
        owner_id = g.user.id
        number = generate_unique_number(type="channel_")
        name = data.get('channel_name')
        if not name :
            return jsonify({"code": 400, "msg": "频道名称不能为空", "data": None}), 400
        description = data.get('channel_desc')
        # 前端上传过头像就用上传的，没传则默认 robohash
        avatar_url = data.get('avatar') or f"https://robohash.org/{number}?set=set3&size=200×200"
        channel = Channel(owner_id=owner_id, number=number, name=name, description=description,avatar = avatar_url)
        db.session.add(channel)
        db.session.flush()
        # 同步到channelMember中
        channel_id = channel.id
        user_id = g.user.id
        role = 1
        notify_level = 1
        member = ChannelMember(channel_id=channel_id,user_id=user_id,role=role,notify_level=notify_level)
        db.session.add(member)
        # 创建即出现在频道主的会话列表，不等第一条帖子
        upsert_conversation(owner_id, 'channel', channel_id, None, '频道已创建')
        # 创建时选择的 Bot：批量绑定
        for bid in (data.get('bot_template_ids') or []):
            try:
                bid = int(bid)
            except (TypeError, ValueError):
                continue
            if BotTemplate.query.get(bid):
                db.session.add(ChannelBot(channel_id=channel_id, bot_template_id=bid, enabled=1))
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "创建频道成功",
            "data": {
                "id": channel.id,
                "owner_id": owner_id,
                "number": number,
                "name": name,
                "description": description,
                "avatar": avatar_url,
                "is_public": 1,
                "status": 1
            }
        }),200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg":f"服务器内部出错,{str(e)}",
            "data":None
        }),500

@bp.route('/join', methods=['GET','POST'])
@login_required
def join_channel():
    """
    加入频道,判断用户是否已经加入该频道,未加入则添加到频道成员表中,已加入则提示已加入
    :param:request.get_json(data),channel_id = data.get('channel_id')
    :return: 成功：返回频道编号、加入用户的编号、频道用户角色、消息提醒
    :return:失败：前端传递信息错误 or 重复加入频道 or 服务器内部错误
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求格式错误，需要JSON", "data": None}), 400
        user_id = g.user.id
        channel_id = data.get('channel_id')
        if not channel_id:
            return jsonify({"code": 400, "msg": "缺少频道编号", "data": None}), 400
        # 校验频道存在且正常
        channel = Channel.query.filter_by(id=channel_id).first()
        if not channel or channel.status != 1:
            return jsonify({"code": 400, "msg": "频道不存在或已失效", "data": None}), 400
        if not ChannelMember.query.filter_by(user_id=user_id,channel_id=channel_id).first():
            user_id = user_id
            # 新成员角色由频道开关决定：默认副频道主(role=2) 或 普通订阅者(role=3)
            role = 2 if channel.default_vice_owner == 1 else 3
            notify_level = 1
            channel_member = ChannelMember(channel_id=channel_id,user_id=user_id,role=role,notify_level=notify_level)
            db.session.add(channel_member)
            # 订阅即出现在会话列表
            upsert_conversation(user_id, 'channel', channel_id, None, '已加入频道')
            db.session.commit()
            return jsonify({"code":0,"msg":"加入频道成功","data":{
                "channel_id":channel_id,
                "user_id":user_id,
                "role":role,
                "notify_level":notify_level
            }}),200
        else:
            # 幂等：已订阅视为成功，让前端直接打开频道；顺带自愈缺失的会话记录
            upsert_conversation(user_id, 'channel', channel_id, None, '已加入频道')
            db.session.commit()
            return jsonify({"code": 0, "msg": "已订阅该频道", "data": {
                "channel_id": channel_id,
                "user_id": user_id,
                "already": True
            }}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"code":500,"msg":f"服务器内部错误,{str(e)}","data":None}), 500

@bp.route('/leave', methods=['POST'])  # 只有POST 才可以使用request.get_json()
@login_required
def leave_channel():
    """
    用户推出频道，查看用户是否在此频道，若是，在频道成员表中删除相关记录，否则给出错误提示
    :param: request.get_json(data),channel_id = data.get('channel_id')
    :return:成功：退出频道成功
    :return:失败：前端传递信息错误 or 不在此频道 or 服务器内部出错
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求格式错误，需要JSON", "data": None}), 400
        user_id = g.user.id
        channel_id = data.get('channel_id')
        will_delete_member = ChannelMember.query.filter_by(user_id=user_id, channel_id=channel_id).first()
        if not will_delete_member:
            return resp_json(code=PARAMS_ERROR, msg="不在频道无法退出", data=None)
        if will_delete_member.role == 1:
            return resp_json(code=PARAMS_ERROR, msg="频道主不能退订自己的频道，可在频道信息中删除频道", data=None)
        db.session.delete(will_delete_member)
        # 退订后频道会话从会话列表移除（否则残留会话点进去既看不了又显示发帖按钮）
        conv = UserConversation.query.filter_by(user_id=user_id, conv_type='channel', target_id=channel_id).first()
        if conv:
            db.session.delete(conv)
        db.session.commit()
        return resp_json(msg="退出频道成功", data=None)
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器错误,{str(e)}", data=None)


@bp.route('/<int:channel_id>/members', methods=['GET'])
@login_required
def get_channel_members(channel_id):
    """频道订阅者列表：供前端内嵌“频道信息”视图使用（含频道基础信息与我的角色）"""
    try:
        member = ChannelMember.query.filter_by(channel_id=channel_id, user_id=g.user.id).first()
        if not member:
            return resp_json(code=PARAMS_ERROR, msg="你未订阅该频道", data=None)
        channel = Channel.query.get(channel_id)
        if not channel:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在", data=None)
        members = ChannelMember.query.filter_by(channel_id=channel_id).order_by(ChannelMember.role, ChannelMember.id).all()
        member_list = []
        for m in members:
            u = User.query.get(m.user_id)
            if not u:
                continue
            member_list.append({
                "user_id": u.id,
                "nickname": u.nickname,
                "avatar_url": u.avatar,
                "role": m.role,  # 1=频道主 2=副频道主 3=订阅者
                "role_name": {1: "频道主", 2: "副频道主", 3: "订阅者"}.get(m.role, "订阅者"),
                "joined_at": m.joined_at.isoformat() if m.joined_at else None
            })
        return resp_json(msg="获取成功", data={
            "members": member_list,
            "total": len(member_list),
            "my_role": member.role,
            "my_notify": member.notify_level,
            "name": channel.name,
            "description": channel.description,
            "avatar": channel.avatar,
            "number": channel.number,
            "default_vice_owner": channel.default_vice_owner if channel.default_vice_owner is not None else 1
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/<int:channel_id>/media', methods=['GET'])
@login_required
def get_channel_media(channel_id):
    """
    频道共享媒体：返回频道内未删除帖子(status=1)发布过的图片/视频/文件
    - 附件存于 ChannelFile，file_type 2=视频 3=图片 其余=其他文件，前端据此分类筛选
    - 按时间升序（最新在最后），支持 before_id 向更早翻页
    - 仅订阅者可见（与 /members 一致）
    查询参数：per_page(默认80，最大200), before_id(可选)
    """
    try:
        member = ChannelMember.query.filter_by(channel_id=channel_id, user_id=g.user.id).first()
        if not member:
            return resp_json(code=PARAMS_ERROR, msg="你未订阅该频道", data=None)
        try:
            per_page = int(request.args.get('per_page', 80))
        except (TypeError, ValueError):
            per_page = 80
        per_page = min(max(per_page, 1), 200)
        before_id = request.args.get('before_id', type=int)

        valid_post_ids = db.session.query(ChannelPost.id).filter(
            ChannelPost.channel_id == channel_id, ChannelPost.status == 1
        )
        query = ChannelFile.query.filter(
            ChannelFile.channel_id == channel_id,
            ChannelFile.post_id.in_(valid_post_ids)
        )
        if before_id:
            query = query.filter(ChannelFile.id < before_id)
        rows = query.order_by(ChannelFile.id.desc()).limit(per_page).all()
        rows.reverse()  # 升序：最新在最后
        media = [{
            "file_id": f.id,
            "file_url": f.file_url,
            "filename": f.filename,
            "file_type": f.file_type,
            "file_size": f.file_size,
            "sender_id": f.sender_id,
            "created_at": f.created_at.isoformat() if f.created_at else None
        } for f in rows]
        return resp_json(msg="获取成功", data={
            "media": media,
            "oldest_id": (rows[0].id if rows else None),
            "has_more": len(rows) == per_page
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/info', methods=['PUT'])
@login_required
def update_channel_info():
    """频道主/副频道主编辑频道信息：名称、简介、头像。
    头像由前端先调用 /upload/image 上传得到 URL，再以 avatar_url 传入（与群聊 /group/info 一致）。
    query: channel_id；body(JSON): {name?, description?, avatar_url?}"""
    try:
        channel_id = request.args.get('channel_id', type=int)
        if not channel_id:
            return resp_json(PARAMS_ERROR, "channel_id 不能为空", data=None)
        member = ChannelMember.query.filter_by(channel_id=channel_id, user_id=g.user.id).first()
        if not member:
            return resp_json(PARAMS_ERROR, "你未订阅该频道", data=None)
        if member.role not in (1, 2):
            return resp_json(PARAMS_ERROR, "只有频道主或副频道主能修改频道信息", data=None)
        channel = Channel.query.get(channel_id)
        if not channel:
            return resp_json(PARAMS_ERROR, "频道不存在", data=None)
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求体不能为空", data=None)
        changed = False
        name = data.get('name')
        if name is not None:
            name = name.strip()
            if not name or len(name) > 50:
                return resp_json(PARAMS_ERROR, "频道名称长度需在1-50之间", data=None)
            channel.name = name
            changed = True
        description = data.get('description')
        if description is not None:
            if len(description) > 200:
                return resp_json(PARAMS_ERROR, "简介不能超过200字", data=None)
            channel.description = description
            changed = True
        avatar_url = data.get('avatar_url')
        if avatar_url is not None:
            channel.avatar = avatar_url.strip()
            changed = True
        if not changed:
            return resp_json(PARAMS_ERROR, "没有可修改的字段", data=None)
        db.session.commit()
        return resp_json(OK, "修改成功", data={
            "channel_id": channel.id,
            "name": channel.name,
            "description": channel.description,
            "avatar": channel.avatar,
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误,{str(e)}", data=None)


@bp.route('/<int:channel_id>/peek', methods=['GET'])
@login_required
def channel_peek(channel_id):
    """频道轻预览卡：供搜索结果点击频道名时使用（不要求已订阅，仅公开频道），返回简介/订阅数/性别构成"""
    try:
        channel = Channel.query.filter_by(id=channel_id, status=1).first()
        if not channel:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在", data=None)
        if channel.is_public != 1:
            return resp_json(code=PARAMS_ERROR, msg="该频道不公开", data=None)
        rows = db.session.query(User.gender, func.count(ChannelMember.id)) \
            .join(ChannelMember, ChannelMember.user_id == User.id) \
            .filter(ChannelMember.channel_id == channel_id) \
            .group_by(User.gender).all()
        dist = {0: 0, 1: 0, 2: 0}
        for gv, c in rows:
            dist[gv if gv is not None else 0] = c
        total = sum(dist.values())
        joined = ChannelMember.query.filter_by(channel_id=channel_id, user_id=g.user.id).first() is not None
        return resp_json(msg="ok", data={
            "channel_id": channel.id, "name": channel.name, "avatar": channel.avatar,
            "description": channel.description, "number": channel.number,
            "subscriber_count": total, "joined": joined,
            "gender": {"unknown": dist[0], "male": dist[1], "female": dist[2]}
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/notify/set', methods=['POST'])
@login_required
def set_channel_notify():
    """设置我对某频道的通知级别：{channel_id, notify_level} 1=正常接收 3=静音（只影响自己）"""
    try:
        data = request.get_json(silent=True)
        channel_id = (data or {}).get('channel_id')
        notify_level = (data or {}).get('notify_level')
        if not channel_id or notify_level is None:
            return resp_json(code=PARAMS_ERROR, msg="缺少 channel_id 或 notify_level", data=None)
        notify_level = int(notify_level)
        if notify_level not in (1, 2, 3):
            return resp_json(code=PARAMS_ERROR, msg="notify_level 必须为 1/2/3", data=None)
        member = ChannelMember.query.filter_by(channel_id=channel_id, user_id=g.user.id).first()
        if not member:
            return resp_json(code=PARAMS_ERROR, msg="你未订阅该频道", data=None)
        member.notify_level = notify_level
        db.session.commit()
        return resp_json(msg="通知设置已更新", data={"channel_id": channel_id, "notify_level": notify_level})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)

@bp.route('/exist_users', methods=['POST'])
@login_required
def get_exist_users():
    """
    供频道主/副频道主使用,先通过check_channel_member_role(channel_id)函数查看当前用户是否不为订阅者，之后可以获取用户列表
    :param:data = request.get_json(data),channel_id = data.get('channel_id'),user_id = g.user.id
    :return: 成功：返回频道中所有存在的用户id、频道内角色、头像
    :return: 失败：前端数据传输失败 or 当前用户为订阅者 or 频道内无人
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="客户端传递数据失败", data=None)
        channel_id = data.get('channel_id')
        if channel_id is None:
            return resp_json(code=PARAMS_ERROR, msg="未提供频道编号", data=None)
        if not check_channel_member_role(channel_id):
            return resp_json(code=PARAMS_ERROR, msg="订阅者无权访问", data=None)
        channel = Channel.query.filter_by(id=channel_id).first()
        owner_id = channel.owner_id
        if not ChannelMember.query.filter((ChannelMember.channel_id == channel_id)&(ChannelMember.user_id != owner_id)).first():
            return resp_json(code=PARAMS_ERROR, msg="没有人订阅，快去推广吧", data=None)
            # member_rows 是 Python列表类型，存储了所有符合条件的记录
        member_rows = ChannelMember.query.filter(ChannelMember.channel_id == channel_id).all()
        users_id_list = []  # 频道内部人员id列表
        avatar_url_dict = {}  # 用户关联的头像字典
        user_role_dict = {}
        for member in member_rows:
            user_id = member.user_id
            users_id_list.append(user_id)  # 将所有频道的用户id 添加到指定列表中，也就是users_id_list
        for user_id in users_id_list:
            user = User.query.filter(User.id == user_id).first()
            user_member = ChannelMember.query.filter((ChannelMember.channel_id == channel_id) & (ChannelMember.user_id == user_id)).first()
            avatar = user.avatar
            user_role_dict[user_id] = user_member.role
            avatar_url_dict[user_id] = avatar
        return resp_json(code=OK, msg="获取频道内用户成功",data={"avatar_url_dict": avatar_url_dict, "user_role_dict": user_role_dict})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误,{str(e)}",data=None)


@bp.route('/publish/post', methods=['POST'])
@login_required
def publish_channel_post():
    """
    频道主/副频道主 发布帖子（支持多文件）
    """
    try:
        channel_id = request.form.get('channel_id')
        author_id = g.user.id

        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少频道ID", data=None)
        # 校验权限
        if not check_channel_member_role(channel_id):
            return resp_json(code=PARAMS_ERROR, msg="订阅者无权发布", data=None)

        title = request.form.get('title')
        content = request.form.get('content')
        if not title or not content:
            return resp_json(code=PARAMS_ERROR, msg="标题或内容不能为空", data=None)
        # 敏感词命中即拦截（不存库）
        if is_user_muted(g.user):
            return resp_json(code=PARAMS_ERROR, msg="你已被禁言，暂时无法发布", data=None)
        ok, smsg = guard_sensitive(title, g.user.id, 'channel')
        if not ok:
            return resp_json(code=PARAMS_ERROR, msg=smsg, data=None)
        ok, smsg = guard_sensitive(content, g.user.id, 'channel')
        if not ok:
            return resp_json(code=PARAMS_ERROR, msg=smsg, data=None)
        # Bot 前置规则（防刷屏/防广告/重复内容/夜间禁言）
        blocked, bmsg = run_bots('channel', channel_id, 'message', 'pre',
                                 {'sender_id': g.user.id, 'sender_name': g.user.nickname,
                                  'text': (title + ' ' + content), 'msg_type': 1})
        if blocked:
            return resp_json(code=PARAMS_ERROR, msg=bmsg, data=None)
        # 创建帖子
        post = ChannelPost(author_id=author_id, channel_id=channel_id, title=title, content=content)
        db.session.add(post)
        db.session.flush()
        post_id = post.id

        # 刷新全体频道成员会话列表（除发布者未读+1）
        touch_channel_conversation(channel_id, post_id, content, author_id)

        # 处理文件（支持多文件）
        files = request.files.getlist('file')
        file_results = []

        for file in files:
            if not file.filename:  # 跳过空文件项
                continue

            file_size = get_file_size(file)
            if file_size is None:
                return resp_json(code=PARAMS_ERROR, msg="文件超出大小限制", data=None)
            result = save_upload_file(file, scope=('channel', channel_id))
            if result is None:
                return resp_json(code=PARAMS_ERROR, msg="文件格式不支持或保存失败", data=None)
            file_path, file_type, filename = result
            channel_file = ChannelFile(
                post_id=post_id,
                channel_id=channel_id,
                sender_id=author_id,
                filename=filename,
                file_url=file_path,
                file_type=file_type,
                file_size=file_size
            )
            db.session.add(channel_file)
            file_results.append({
                "file_id": channel_file.id,
                "filename": filename,
                "file_url": file_path,
                "file_type": file_type
            })
        db.session.commit()
        # 实时广播给在线的频道成员（帖子流），payload 与前端 receive_post 处理对齐
        socketio.emit('receive_post', {
            "id": post_id, "post_id": post_id, "channel_id": int(channel_id),
            "author_id": author_id, "author_nickname": g.user.nickname,
            "title": title, "content": content,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "comment_count": 0, "files": file_results,
        }, room=f"channel_{channel_id}")
        return resp_json(code=OK, msg="发布帖子成功", data={
            "post_id": post_id,
            "channel_id": channel_id,
            "author_id": author_id,
            "title": title,
            "content": content,
            "files": file_results,
            "file_count": len(file_results)
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"发布帖子失败: {e}")
        return resp_json(code=SERVER_ERROR, msg="服务器内部错误", data=None)

@bp.route('/post/attach', methods=['POST'])
@login_required
def attach_channel_post_file():
    """为已发布的帖子后台追加附件（非阻塞发帖第二步）：multipart {post_id, file}
    仅允许帖子作者且为频道主/副频道主时上传；成功后广播 post_file_added 实时刷新卡片。"""
    try:
        post_id = request.form.get('post_id')
        if not post_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 post_id", data=None)
        post = ChannelPost.query.filter_by(id=post_id, status=1).first()
        if not post:
            return resp_json(code=PARAMS_ERROR, msg="帖子不存在或已删除", data=None)
        # 只能给自己的帖子追加附件
        if post.author_id != g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="只能为自己的帖子上传附件", data=None)
        # 发布人需仍具备发帖权限（频道主/副频道主）
        if not check_channel_member_role(post.channel_id):
            return resp_json(code=PARAMS_ERROR, msg="订阅者无权上传附件", data=None)
        file = request.files.get('file')
        if not file or not file.filename:
            return resp_json(code=PARAMS_ERROR, msg="没有文件", data=None)
        file_size = get_file_size(file)
        if file_size is None:
            return resp_json(code=PARAMS_ERROR, msg="文件超出大小限制", data=None)
        result = save_upload_file(file, scope=('channel', post.channel_id))
        if result is None:
            return resp_json(code=PARAMS_ERROR, msg="文件格式不支持或保存失败", data=None)
        file_path, file_type, filename = result
        channel_file = ChannelFile(
            post_id=post_id,
            channel_id=post.channel_id,
            sender_id=g.user.id,
            filename=filename,
            file_url=file_path,
            file_type=file_type,
            file_size=file_size
        )
        db.session.add(channel_file)
        db.session.commit()
        payload = {
            "post_id": int(post_id), "channel_id": int(post.channel_id),
            "file": {"file_id": channel_file.id, "filename": filename,
                     "file_url": file_path, "file_type": file_type, "file_size": file_size},
        }
        socketio.emit('post_file_added', payload, room=f"channel_{post.channel_id}")
        return resp_json(code=OK, msg="附件上传成功", data=payload["file"])
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"追加帖子附件失败: {e}")
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/post/get', methods=['POST','GET'])
@login_required
def get_channel_post():
    """
    频道内成员获取帖子
    :param: request.get_json(data),channel_id = data.get('channel_id'),channel.posts,channel_post.channel_files
    :return: 成功：返回频道内部帖子和文件
    :return: 失败：前端数据错误 or 缺少频道编号 or 一条帖子没有 or 数据库内部错误
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="客户端传递消息失败", data=None)
        channel_id = data.get('channel_id')
        if not channel_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少频道信息", data=None)
        channel = Channel.query.options(db.joinedload(Channel.posts).joinedload(ChannelPost.channel_files)).filter_by(id=channel_id).first()
        channel_post = channel.posts # relationship,获取相关频道的所有帖子
        active_posts = [p for p in channel_post if p.status == 1]
        # 批量取作者昵称，避免 N+1
        author_ids = list({p.author_id for p in active_posts})
        author_map = {u.id: u.nickname for u in User.query.filter(User.id.in_(author_ids)).all()} if author_ids else {}
        # 批量统计每帖未删除评论数（帖子流卡片显示“N 评论”入口）
        post_ids = [p.id for p in active_posts]
        comment_count_map = {}
        if post_ids:
            for pid, cnt in db.session.query(ChannelComment.post_id, func.count(ChannelComment.id)).filter(
                ChannelComment.post_id.in_(post_ids), ChannelComment.deleted_at.is_(None)
            ).group_by(ChannelComment.post_id).all():
                comment_count_map[pid] = cnt
        post_list = []
        for post in active_posts:
            post_data = {
                "post_id":post.id,
                "title":post.title,
                "content":post.content,
                "author_id":post.author_id,
                "author_nickname":author_map.get(post.author_id),
                "view_count":post.view_count,
                "comment_count":comment_count_map.get(post.id, 0),
                "created_at": post.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }
            post_list.append(post_data)
        file = []
        # channel_post是帖子列表，要通过每个post的channel_files关系取文件（已通过joinedload预加载）
        for post in channel_post:
            if post.status != 1:
                continue
            for channel_files in post.channel_files:
                channel_file ={
                    "post_id":channel_files.post_id,
                    "file_id":channel_files.id,
                    "filename":channel_files.filename,
                    "file_url":channel_files.file_url,
                    "file_type":channel_files.file_type,
                    "sender_id":channel_files.sender_id
                }
                file.append(channel_file)
        if not channel_post:
            # 空频道不是错误：正常返回空列表，由前端渲染“引导发帖”空状态（避免每次切进来都弹 toast）
            return resp_json(msg="频道暂无帖子", data={"channel_post": [], "channel_file": []})
        return resp_json(msg="获取帖子成功",data={"channel_post": post_list, "channel_file": file})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误,{str(e)}",data=None)

@bp.route('/post/detail', methods=['POST'])
@login_required
def get_channel_post_detail():
    """
    获取单条帖子详情，并累加浏览量
    前端传：post_id
    浏览量规则：同一用户60秒内重复打开同一帖子只计一次，防止刷量
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="客户端传递消息失败", data=None)
        post_id = data.get('post_id')
        if not post_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少帖子编号", data=None)

        # ===== 校验帖子存在且未删除 =====
        post = ChannelPost.query.filter_by(id=post_id, status=1).first()
        if not post:
            return resp_json(code=PARAMS_ERROR, msg="帖子不存在或已删除", data=None)

        # ===== 帖子详情与评论：未加入频道也可查看（仅发布评论需订阅，见 send_channel_comment）=====

        # ===== 浏览量+1（原子UPDATE，60秒防刷） =====
        view_key = f'viewed_post_{post_id}'
        last_view_at = session.get(view_key)
        now = datetime.datetime.now(datetime.timezone.utc)
        if last_view_at is None or (now - datetime.datetime.fromtimestamp(last_view_at, datetime.timezone.utc)).total_seconds() >= 60:
            db.session.execute(
                db.update(ChannelPost)
                .where(ChannelPost.id == post_id)
                .values(view_count=ChannelPost.view_count + 1)
            )
            db.session.commit()
            session[view_key] = now.timestamp()
            post = ChannelPost.query.get(post_id)  # 重新查询返回最新浏览量

        # ===== 帖子关联文件 =====
        file_list = [{
            "file_id": f.id,
            "filename": f.filename,
            "file_url": f.file_url,
            "file_type": f.file_type,
            "file_size": f.file_size,
            "sender_id": f.sender_id
        } for f in post.channel_files]

        # ===== 评论列表（按时间正序，带作者昵称） =====
        comment_rows = ChannelComment.query.filter_by(post_id=post_id).filter(ChannelComment.deleted_at.is_(None)).order_by(ChannelComment.created_at.asc()).all()
        c_user_ids = list({c.user_id for c in comment_rows})
        c_users = {u.id: u for u in User.query.filter(User.id.in_(c_user_ids)).all()} if c_user_ids else {}
        c_by_id = {c.id: c for c in comment_rows}
        comments = [{
            "comment_id": c.id,
            "user_id": c.user_id,
            "user_nickname": c_users[c.user_id].nickname if c.user_id in c_users else None,
            "user_avatar": c_users[c.user_id].avatar if c.user_id in c_users else None,
            "content": c.content,
            "reply_to": c.reply_to,
            "reply_to_nickname": (c_users.get(c_by_id[c.reply_to].user_id).nickname if c.reply_to in c_by_id and c_users.get(c_by_id[c.reply_to].user_id) else None) if c.reply_to else None,
            "reply_to_content": (c_by_id[c.reply_to].content if c.reply_to in c_by_id else None) if c.reply_to else None,
            "created_at": c.created_at.strftime('%Y-%m-%d %H:%M:%S')
        } for c in comment_rows]

        author = User.query.get(post.author_id)
        return resp_json(msg="获取帖子详情成功", data={
            "post_id": post.id,
            "channel_id": post.channel_id,
            "title": post.title,
            "content": post.content,
            "author_id": post.author_id,
            "author_nickname": author.nickname if author else None,
            "view_count": post.view_count,
            "created_at": post.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            "files": file_list,
            "comments": comments
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)

#------------------------------------------------------------------------------------------
@bp.route('/post/forward', methods=['POST'])
@login_required
def forward_channel_post():
    """订阅者把频道帖子转发到私聊或群聊：生成一条 msg_type=5 的转发卡片消息（content 存 JSON）。
    前端点击卡片跳转到该频道并打开帖子详情。
    data = {post_id, target_type: 'private'|'group', target_id}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="请求参数为空", data=None)
        post_id = data.get('post_id')
        target_type = data.get('target_type')
        target_id = data.get('target_id')
        if not post_id or not target_type or not target_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 post_id/target_type/target_id", data=None)
        if target_type not in ('private', 'group'):
            return resp_json(code=PARAMS_ERROR, msg="target_type 仅支持 private 或 group", data=None)
        post = ChannelPost.query.filter_by(id=post_id, status=1).first()
        if not post:
            return resp_json(code=PARAMS_ERROR, msg="帖子不存在或已删除", data=None)
        channel = Channel.query.filter_by(id=post.channel_id, status=1).first()
        if not channel:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在或已失效", data=None)
        # 转发者必须是该频道成员（频道主/副/订阅者）
        if not ChannelMember.query.filter_by(channel_id=channel.id, user_id=g.user.id).first():
            return resp_json(code=PARAMS_ERROR, msg="订阅该频道后才能转发其帖子", data=None)
        # 构造转发卡片（封面取首张图片）
        author = User.query.get(post.author_id)
        cover = None
        for f in post.channel_files:
            if f.file_type == 3:
                cover = f.file_url
                break
        summary = (post.content or '')[:120]
        card = {
            "kind": "channel_post",
            "post_id": post.id,
            "channel_id": channel.id,
            "channel_name": channel.name,
            "channel_avatar": channel.avatar,
            "author_nickname": author.nickname if author else None,
            "title": post.title or '',
            "summary": summary,
            "cover": cover,
        }
        card_json = json.dumps(card, ensure_ascii=False)
        preview = ("[转发] " + ((post.title or '') or summary or channel.name)[:60])
        me_id = g.user.id
        target_id = int(target_id)

        if target_type == 'private':
            if target_id == me_id:
                return resp_json(code=PARAMS_ERROR, msg="不能转发给自己", data=None)
            receiver = User.query.filter_by(id=target_id, status=1).first()
            if receiver is None or receiver.deleted_at is not None:
                return resp_json(code=PARAMS_ERROR, msg="接收用户不存在", data=None)
            if Blacklist.query.filter_by(user_id=target_id, block_type='user', target_id=me_id).first():
                return resp_json(code=PARAMS_ERROR, msg="你已被对方拉黑，无法发送", data=None)
            if Blacklist.query.filter_by(user_id=me_id, block_type='user', target_id=target_id).first():
                return resp_json(code=PARAMS_ERROR, msg="你已拉黑对方，请先取消拉黑", data=None)
            conv = Conversation.query.filter(
                ((Conversation.user1_id == me_id) & (Conversation.user2_id == target_id)) |
                ((Conversation.user1_id == target_id) & (Conversation.user2_id == me_id))
            ).first()
            if not conv:
                conv = Conversation(user1_id=me_id, user2_id=target_id)
                db.session.add(conv)
                db.session.flush()
            message = Message(sender_id=me_id, receiver_id=target_id, content=card_json, msg_type=5)
            db.session.add(message)
            db.session.flush()
            conv.last_msg_id = message.id
            touch_private_conversation(me_id, target_id, message.id, preview)
            db.session.commit()
            socketio.emit("message", {"from_user": me_id, "msg_type": 5, "msg_id": message.id, "content": card_json}, room=str(target_id))
        else:  # group
            group = Group.query.filter_by(id=target_id, status=1).first()
            if not group:
                return resp_json(code=PARAMS_ERROR, msg="群聊不存在或已解散", data=None)
            member = GroupMember.query.filter_by(group_id=target_id, user_id=me_id).first()
            if not member:
                return resp_json(code=PARAMS_ERROR, msg="你不是群成员，无法转发到该群", data=None)
            now = datetime.datetime.utcnow()
            if member.mute_until and member.mute_until > now:
                return resp_json(code=PARAMS_ERROR, msg="你已被禁言，暂时无法发言", data=None)
            if group.mute_all and member.role not in (1, 2):
                return resp_json(code=PARAMS_ERROR, msg="群已开启全员禁言", data=None)
            message = GroupMessage(sender_id=me_id, group_id=target_id, content=card_json, msg_type=5)
            db.session.add(message)
            db.session.flush()
            touch_group_conversation(target_id, message.id, preview, me_id)
            db.session.commit()
            socketio.emit("receive_message", {
                "id": message.id, "sender_id": me_id, "sender_name": g.user.nickname,
                "sender_avatar": g.user.avatar, "group_id": target_id,
                "content": card_json, "msg_type": 5, "created_at": message.created_at.isoformat(),
            }, room=f"group_{target_id}")

        return resp_json(code=OK, msg="已转发", data={
            "msg_id": message.id, "msg_type": 5, "content": card_json,
            "created_at": message.created_at.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"转发帖子失败: {e}")
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/share', methods=['POST'])
@login_required
def share_channel():
    """订阅者把频道本身分享（转发）到私聊或群聊：生成一条 msg_type=5 的频道卡片消息（content 存 JSON，kind='channel'）。
    与转发帖子同一机制，前端点击卡片：已订阅→打开频道；未订阅→预览并引导订阅。
    data = {channel_id, target_type: 'private'|'group', target_id}
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="请求参数为空", data=None)
        channel_id = data.get('channel_id')
        target_type = data.get('target_type')
        target_id = data.get('target_id')
        if not channel_id or not target_type or not target_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 channel_id/target_type/target_id", data=None)
        if target_type not in ('private', 'group'):
            return resp_json(code=PARAMS_ERROR, msg="target_type 仅支持 private 或 group", data=None)
        channel = Channel.query.filter_by(id=channel_id, status=1).first()
        if not channel:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在或已失效", data=None)
        # 分享者必须是该频道成员（频道主/副/订阅者）
        if not ChannelMember.query.filter_by(channel_id=channel.id, user_id=g.user.id).first():
            return resp_json(code=PARAMS_ERROR, msg="订阅该频道后才能分享", data=None)
        member_count = ChannelMember.query.filter_by(channel_id=channel.id).count()
        card = {
            "kind": "channel",
            "channel_id": channel.id,
            "channel_name": channel.name,
            "channel_avatar": channel.avatar,
            "description": channel.description or '',
            "member_count": member_count,
        }
        card_json = json.dumps(card, ensure_ascii=False)
        preview = ("[频道] " + (channel.name or '')[:60])
        me_id = g.user.id
        target_id = int(target_id)

        if target_type == 'private':
            if target_id == me_id:
                return resp_json(code=PARAMS_ERROR, msg="不能分享给自己", data=None)
            receiver = User.query.filter_by(id=target_id, status=1).first()
            if receiver is None or receiver.deleted_at is not None:
                return resp_json(code=PARAMS_ERROR, msg="接收用户不存在", data=None)
            if Blacklist.query.filter_by(user_id=target_id, block_type='user', target_id=me_id).first():
                return resp_json(code=PARAMS_ERROR, msg="你已被对方拉黑，无法发送", data=None)
            if Blacklist.query.filter_by(user_id=me_id, block_type='user', target_id=target_id).first():
                return resp_json(code=PARAMS_ERROR, msg="你已拉黑对方，请先取消拉黑", data=None)
            conv = Conversation.query.filter(
                ((Conversation.user1_id == me_id) & (Conversation.user2_id == target_id)) |
                ((Conversation.user1_id == target_id) & (Conversation.user2_id == me_id))
            ).first()
            if not conv:
                conv = Conversation(user1_id=me_id, user2_id=target_id)
                db.session.add(conv)
                db.session.flush()
            message = Message(sender_id=me_id, receiver_id=target_id, content=card_json, msg_type=5)
            db.session.add(message)
            db.session.flush()
            conv.last_msg_id = message.id
            touch_private_conversation(me_id, target_id, message.id, preview)
            db.session.commit()
            socketio.emit("message", {"from_user": me_id, "msg_type": 5, "msg_id": message.id, "content": card_json}, room=str(target_id))
        else:  # group
            group = Group.query.filter_by(id=target_id, status=1).first()
            if not group:
                return resp_json(code=PARAMS_ERROR, msg="群聊不存在或已解散", data=None)
            member = GroupMember.query.filter_by(group_id=target_id, user_id=me_id).first()
            if not member:
                return resp_json(code=PARAMS_ERROR, msg="你不是群成员，无法分享到该群", data=None)
            now = datetime.datetime.utcnow()
            if member.mute_until and member.mute_until > now:
                return resp_json(code=PARAMS_ERROR, msg="你已被禁言，暂时无法发言", data=None)
            if group.mute_all and member.role not in (1, 2):
                return resp_json(code=PARAMS_ERROR, msg="群已开启全员禁言", data=None)
            message = GroupMessage(sender_id=me_id, group_id=target_id, content=card_json, msg_type=5)
            db.session.add(message)
            db.session.flush()
            touch_group_conversation(target_id, message.id, preview, me_id)
            db.session.commit()
            socketio.emit("receive_message", {
                "id": message.id, "sender_id": me_id, "sender_name": g.user.nickname,
                "sender_avatar": g.user.avatar, "group_id": target_id,
                "content": card_json, "msg_type": 5, "created_at": message.created_at.isoformat(),
            }, room=f"group_{target_id}")

        return resp_json(code=OK, msg="已分享", data={
            "msg_id": message.id, "msg_type": 5, "content": card_json,
            "created_at": message.created_at.isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"分享频道失败: {e}")
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/post/delete', methods=['POST'])
@login_required
def delete_channel_post():
    """
    删除频道帖子（仅频道主）：级联清理该帖的附件(磁盘)、评论、点赞，最后删帖子本身
    data = {"post_id": 123}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="请求参数为空", data=None)
        post_id = data.get('post_id')
        if not post_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 post_id 参数", data=None)
        post = ChannelPost.query.get(post_id)
        if not post:
            return resp_json(code=404, msg="帖子不存在", data=None)
        # 权限：频道主(role==1)可删除任意帖子；副频道主(role==2)仅可删除自己发布的帖子
        member = ChannelMember.query.filter_by(user_id=g.user.id, channel_id=post.channel_id).first()
        if not member or member.role not in (1, 2):
            return resp_json(code=PARAMS_ERROR, msg="仅频道主或副频道主可删除帖子", data=None)
        if member.role == 2 and post.author_id != g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="副频道主只能删除自己发布的帖子", data=None)

        # 1. 先删除帖子附件的磁盘实体，再删附件记录
        files = ChannelFile.query.filter_by(post_id=post_id).all()
        for cf in files:
            delete_old_file(cf.file_url)
            db.session.delete(cf)
        # 2. 清理评论与点赞
        ChannelComment.query.filter_by(post_id=post_id).delete()
        ChannelLike.query.filter_by(post_id=post_id).delete()
        # 3. 删除帖子本身
        db.session.delete(post)
        db.session.commit()
        return resp_json(code=OK, msg="帖子已删除", data={"post_id": int(post_id)})
    except Exception as e:
        db.session.rollback()
        print(f"删除频道帖子失败：{e}")
        return resp_json(code=SERVER_ERROR, msg=f"服务器错误：{str(e)}", data=None)


#------------------------------------------------------------------------------------------
@bp.route('/remove_member', methods=['POST'])
@login_required
def remove_member():
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传递数据为空", data=None)
        channel_id = data.get('channel_id')
        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少必要信息-频道编号", data=None)
        if not check_channel_member_role(channel_id=channel_id):
            return resp_json(code=PARAMS_ERROR,msg="无权操作", data=None)
        delete_user_id = data.get('target_user_id')
        if not delete_user_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少必要信息-要踢成员编号", data=None)
        if not ChannelMember.query.filter_by(user_id=delete_user_id, channel_id=channel_id).first():
            return resp_json(code=PARAMS_ERROR, msg='非频道用户', data=None)
        user = ChannelMember.query.filter_by(user_id=delete_user_id,channel_id=channel_id).first()
        if user.role == 1:
            return resp_json(code=PARAMS_ERROR, msg="不能踢出频道主", data=None)
        db.session.delete(user)
        db.session.commit()
        return resp_json(msg="删除用户成功", data={"target_user_id": delete_user_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"数据库内部错误，{str(e)}",data=None)


#------------------------------------------------------------------------------------------
@bp.route('/comment/delete', methods=['POST'])
@login_required
def delete_channel_comment():
    """
    删除频道帖子评论（软删除）
    - 只能删除自己发送的评论
    - 频道主/副频道主可以删除所有成员的评论
    data = {"comment_id": 123}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        
        comment_id = data.get('comment_id')
        if not comment_id:
            return resp_json(code=400, msg="缺少 comment_id 参数", data=None)
        
        # 查找评论
        comment = ChannelComment.query.get(comment_id)
        if not comment:
            return resp_json(code=404, msg="评论不存在", data=None)
        
        # 验证权限
        member = ChannelMember.query.filter_by(user_id=g.user.id, channel_id=comment.channel_id).first()
        if not member:
            return resp_json(code=403, msg="你不是该频道成员", data=None)
        
        # 频道主/副频道主可以删除任意评论，否则只能删除自己的
        if member.role != 3 and comment.user_id != g.user.id:
            return resp_json(code=403, msg="无权删除该评论", data=None)
        
        # 软删除：标记 deleted_at 时间
        comment.deleted_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        
        return resp_json(code=0, msg="评论已删除", data=None)
        
    except Exception as e:
        db.session.rollback()
        print(f"删除频道评论失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/comment/withdraw', methods=['POST'])
@login_required
def withdraw_channel_comment():
    """
    撤回频道帖子评论
    - 只能撤回自己发送的评论
    - 频道主/副频道主可以撤回所有成员的评论
    data = {"comment_id": 123}
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        
        comment_id = data.get('comment_id')
        if not comment_id:
            return resp_json(code=400, msg="缺少 comment_id 参数", data=None)
        
        # 查找评论
        comment = ChannelComment.query.get(comment_id)
        if not comment:
            return resp_json(code=404, msg="评论不存在", data=None)
        
        # 验证权限
        member = ChannelMember.query.filter_by(user_id=g.user.id, channel_id=comment.channel_id).first()
        if not member:
            return resp_json(code=403, msg="你不是该频道成员", data=None)
        
        # 频道主/副频道主可以撤回任意评论，否则只能撤回自己的
        if member.role != 3 and comment.user_id != g.user.id:
            return resp_json(code=403, msg="无权撤回该评论", data=None)
        
        # 撤回时限：普通订阅者仅能撤回自己 2 分钟内的评论；频道主/副频道主不受限
        if member.role == 3:
            now = datetime.datetime.now(datetime.timezone.utc)
            created = comment.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            if created is not None and (now - created).total_seconds() > 120:
                return resp_json(code=400, msg="超过 2 分钟，无法撤回", data=None)
        
        # 保存原内容供撤回后使用
        original_content = comment.content
        
        # 更新内容：用特殊标记替换
        comment.content = f"[撤回了一条评论] - 撤回时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        db.session.commit()
        
        return resp_json(code=0, msg="评论已撤回", data={
            "original_content": original_content,  # 可选：返回原内容用于重新编辑
            "comment_id": comment_id
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"撤回频道评论失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/comment/edit', methods=['POST'])
@login_required
def edit_channel_comment():
    """
    编辑撤回的频道评论或重新发布的评论
    - 需要 comment_id 和新的 content
    - 只能编辑自己发送的评论
    - 频道主/副频道主可以编辑任意评论
    data = {
        "comment_id": 123,
        "content": "新的评论内容"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=400, msg="请求参数为空", data=None)
        
        comment_id = data.get('comment_id')
        new_content = data.get('content')
        
        if not all([comment_id, new_content]):
            return resp_json(code=400, msg="缺少必要参数", data=None)
        
        # 查找评论
        comment = ChannelComment.query.get(comment_id)
        if not comment:
            return resp_json(code=404, msg="评论不存在", data=None)
        
        # 验证权限
        member = ChannelMember.query.filter_by(user_id=g.user.id, channel_id=comment.channel_id).first()
        if not member:
            return resp_json(code=403, msg="你不是该频道成员", data=None)
        
        # 频道主/副频道主可以编辑任意评论，否则只能编辑自己的
        if member.role != 3 and comment.user_id != g.user.id:
            return resp_json(code=403, msg="无权编辑该评论", data=None)
        
        # 更新内容
        comment.content = new_content
        
        db.session.commit()
        
        return resp_json(code=0, msg="评论已编辑", data={
            "comment_id": comment_id,
            "content": comment.content
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"编辑频道评论失败：{e}")
        return resp_json(code=500, msg=f"服务器错误：{str(e)}", data=None)


@bp.route('/remove_channel', methods=['POST'])
@login_required
def remove_channel():
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传递数据为空", data=None)
        channel_id = data.get('channel_id')
        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少必要信息-频道编号", data=None)
        # 校验：只有频道主(role=1)能解散
        user_id = g.user.id
        member = ChannelMember.query.filter_by(user_id=user_id, channel_id=channel_id).first()
        if not member or member.role != 1:
            return resp_json(code=PARAMS_ERROR, msg="只有频道主可以解散频道", data=None)

        # 确认频道存在
        channel = Channel.query.filter_by(id=channel_id).first()
        if not channel:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在", data=None)

        # ===== 1. 遍历频道文件，删除磁盘文件（相对项目根目录解析） =====
        channel_files = ChannelFile.query.filter_by(channel_id=channel_id).all()
        for cf in channel_files:
            delete_old_file(cf.file_url)
        # ===== 2. 删除所有子表记录（先删依赖 posts 的，再删 posts）=====
        post_id_subquery = db.session.query(ChannelPost.id).filter(ChannelPost.channel_id == channel_id)
        ChannelFile.query.filter_by(channel_id=channel_id).delete()
        ChannelLike.query.filter(ChannelLike.post_id.in_(post_id_subquery)).delete(synchronize_session=False)
        ChannelComment.query.filter_by(channel_id=channel_id).delete()
        ChannelAnnouncement.query.filter_by(channel_id=channel_id).delete()
        ChannelPost.query.filter_by(channel_id=channel_id).delete()
        ChannelMember.query.filter_by(channel_id=channel_id).delete()
        # ===== 3. 最后删频道本身 =====
        db.session.delete(channel)
        db.session.commit()

        return resp_json(msg="频道已解散", data={"channel_id": channel_id})

    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/permission/set', methods=['POST'])
@login_required
def channel_permission_set():
    """
    频道主赋予/撤销副频道主
    前端传：channel_id, target_user_id, action("promote" | "demote")
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传递数据为空", data=None)
        channel_id = data.get('channel_id')
        target_user_id = data.get('target_user_id')
        action = data.get('action')  # "promote" 或 "demote"

        # ===== 参数校验 =====
        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少频道编号", data=None)
        if not target_user_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少目标用户编号", data=None)
        if not action or action not in ('promote', 'demote'):
            return resp_json(code=PARAMS_ERROR, msg="action 必须为 promote 或 demote", data=None)
        # 不能对自己操作
        if int(target_user_id) == g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="不能对自己进行此操作", data=None)
        # ===== 校验当前用户是频道主 =====
        operator = ChannelMember.query.filter_by(
            user_id=g.user.id, channel_id=channel_id
        ).first()
        if not operator or operator.role != 1:
            return resp_json(code=PARAMS_ERROR, msg="只有频道主可以管理角色", data=None)
        # ===== 校验目标用户在此频道 =====
        target_member = ChannelMember.query.filter_by(
            user_id=target_user_id, channel_id=channel_id
        ).first()
        if not target_member:
            return resp_json(code=PARAMS_ERROR, msg="目标用户不在该频道", data=None)
        # 不能对频道主操作
        if target_member.role == 1:
            return resp_json(code=PARAMS_ERROR, msg="不能对频道主进行此操作", data=None)
        # ===== 执行操作 =====
        if action == 'promote':
            # 已经是副频道主
            if target_member.role == 2:
                return resp_json(code=PARAMS_ERROR, msg="该用户已是副频道主", data=None)
            target_member.role = 2
            msg = "已设为副频道主"
        else:  # demote
            # 本来就不是副频道主
            if target_member.role != 2:
                return resp_json(code=PARAMS_ERROR, msg="该用户不是副频道主，无需撤销", data=None)
            target_member.role = 3
            msg = "已撤销副频道主"
        db.session.commit()
        return resp_json(msg=msg, data={
            "channel_id": channel_id,
            "target_user_id": target_user_id,
            "new_role": target_member.role
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)

@bp.route('/default_vice/set', methods=['POST'])
@login_required
def set_channel_default_vice():
    """频道主开关：新加入成员是否默认为副频道主。{channel_id, enabled(1/0)}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="请求参数为空", data=None)
        channel_id = data.get('channel_id')
        enabled = data.get('enabled')
        if not channel_id or enabled is None:
            return resp_json(code=PARAMS_ERROR, msg="缺少 channel_id 或 enabled", data=None)
        enabled = int(enabled)
        if enabled not in (0, 1):
            return resp_json(code=PARAMS_ERROR, msg="enabled 必须为 0 或 1", data=None)
        operator = ChannelMember.query.filter_by(user_id=g.user.id, channel_id=channel_id).first()
        if not operator or operator.role != 1:
            return resp_json(code=PARAMS_ERROR, msg="只有频道主可以设置", data=None)
        channel = Channel.query.get(channel_id)
        if not channel:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在", data=None)
        channel.default_vice_owner = enabled
        db.session.commit()
        return resp_json(msg="设置已更新", data={"channel_id": channel_id, "default_vice_owner": enabled})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/post/comment', methods=['POST'])
@login_required
def comment_post():
    """
    频道帖子评论
    前端传：channel_id, post_id, content
    """
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传递数据为空", data=None)

        channel_id = data.get('channel_id')
        post_id = data.get('post_id')
        content = data.get('content')

        # ===== 参数校验 =====
        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少频道编号", data=None)
        if not post_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少帖子编号", data=None)
        if not content or not content.strip():
            return resp_json(code=PARAMS_ERROR, msg="评论内容不能为空", data=None)

        # ===== 校验用户在频道内 =====
        member = ChannelMember.query.filter_by(
            user_id=g.user.id, channel_id=channel_id
        ).first()
        if not member:
            return resp_json(code=PARAMS_ERROR, msg="你不在该频道中，无法评论", data=None)

        # ===== 校验帖子存在且属于该频道 =====
        post = ChannelPost.query.filter_by(id=post_id, channel_id=channel_id).first()
        if not post:
            return resp_json(code=PARAMS_ERROR, msg="帖子不存在", data=None)

        # ===== 存评论（敏感词命中即拦截，不存库） =====
        safe_content = content.strip()
        if is_user_muted(g.user):
            return resp_json(code=PARAMS_ERROR, msg="你已被禁言，暂时无法评论", data=None)
        ok, smsg = guard_sensitive(safe_content, g.user.id, 'channel')
        if not ok:
            return resp_json(code=PARAMS_ERROR, msg=smsg, data=None)
        # Bot 前置规则（防刷屏/防广告/重复内容/夜间禁言）
        blocked, bmsg = run_bots('channel', channel_id, 'message', 'pre',
                                 {'sender_id': g.user.id, 'sender_name': g.user.nickname,
                                  'text': safe_content, 'msg_type': 1})
        if blocked:
            return resp_json(code=PARAMS_ERROR, msg=bmsg, data=None)
        comment = ChannelComment(
            post_id=post_id,
            channel_id=channel_id,
            user_id=g.user.id,
            content=safe_content
        )
        db.session.add(comment)
        # 通知帖子作者（不通知自己）
        if post.author_id != g.user.id:
            create_notification(
                user_id=post.author_id, type="reply", title="你的帖子收到新评论",
                content=safe_content[:100], related_id=post_id, related_type="channel_post"
            )
        db.session.commit()

        return resp_json(msg="评论成功", data={
            "comment_id": comment.id,
            "post_id": post_id,
            "channel_id": channel_id,
            "content": comment.content,
            "created_at": comment.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })

    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


# --------------------------- 频道公告（发布 / 最新未读 / 我已知晓） ---------------------------
@bp.route('/publish/announcement', methods=['POST'])
@login_required
def publish_channel_announcement():
    """频道主/副频道主发布公告"""
    try:
        data = request.get_json()
        channel_id = (data or {}).get('channel_id')
        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少频道信息", data=None)
        content = (data.get('content') or '').strip()
        if not content:
            return resp_json(code=PARAMS_ERROR, msg="缺少公告内容", data=None)
        if not check_channel_member_role(channel_id):
            return resp_json(code=PARAMS_ERROR, msg="无权发布公告或非频道成员", data=None)
        title = (data.get('title') or '').strip() or '频道公告'
        ok, smsg = guard_sensitive(content, g.user.id, 'channel')
        if not ok:
            return resp_json(code=PARAMS_ERROR, msg=smsg, data=None)
        announcement = ChannelAnnouncement(channel_id=channel_id, publisher_id=g.user.id,
                                           title=title, content=content)
        db.session.add(announcement)
        db.session.flush()
        # 发布者本人视为已读
        db.session.add(ChannelAnnouncementUserIsRead(user_id=g.user.id, channel_id=channel_id,
                                                     announcement_id=announcement.id))
        db.session.commit()
        return resp_json(msg="发布公告成功", data={
            "announcement_id": announcement.id,
            "channel_id": channel_id,
            "title": title,
            "content": content
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/announcement/latest', methods=['POST'])
@login_required
def latest_channel_announcement():
    """打开频道时拉取：仅置顶全频道最新的一条公告；若该条已被本人知晓则不再置顶（不回退到旧公告，无则 data=None）"""
    try:
        data = request.get_json()
        channel_id = (data or {}).get('channel_id')
        if not channel_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少频道信息", data=None)
        if not ChannelMember.query.filter_by(channel_id=channel_id, user_id=g.user.id).first():
            return resp_json(code=PARAMS_ERROR, msg="你未订阅该频道", data=None)
        # 只取全频道最新一条未删除公告（不分读/未读）
        anno = ChannelAnnouncement.query.filter_by(channel_id=channel_id, is_deleted=0).order_by(ChannelAnnouncement.id.desc()).first()
        if not anno:
            return resp_json(data=None)
        # 该最新公告本人已知晓则不再置顶，避免回退显示旧公告
        if ChannelAnnouncementUserIsRead.query.filter_by(user_id=g.user.id, announcement_id=anno.id).first():
            return resp_json(data=None)
        publisher = User.query.get(anno.publisher_id)
        return resp_json(data={
            "announcement_id": anno.id,
            "channel_id": channel_id,
            "title": anno.title,
            "content": anno.content,
            "publisher_id": anno.publisher_id,
            "publisher_nickname": publisher.nickname if publisher else '未知',
            "created_at": anno.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/announcement/get', methods=['POST'])
@login_required
def read_channel_announcement():
    """“我已知晓”上报（幂等）"""
    try:
        data = request.get_json()
        channel_id = (data or {}).get('channel_id')
        announcement_id = (data or {}).get('announcement_id')
        if not channel_id or not announcement_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少参数", data=None)
        if ChannelAnnouncementUserIsRead.query.filter_by(announcement_id=announcement_id,
                                                         user_id=g.user.id).first():
            return resp_json(data=None)  # 幂等：重复上报视为知晓成功
        db.session.add(ChannelAnnouncementUserIsRead(user_id=g.user.id, channel_id=channel_id,
                                                     announcement_id=announcement_id))
        db.session.commit()
        return resp_json(msg="已知晓", data=None)
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


# --------------------------- 频道邀请（与群邀请同构，需被邀请人确认） ---------------------------
def _utcnow_naive():
    """与库中 created_at 一致：MySQL DATETIME 存的是去时区的 UTC 朴素时间"""
    return datetime.datetime.utcnow()


@bp.route('/invite', methods=['POST'])
@login_required
def invite_channel_member():
    """邀请用户订阅频道（需对方确认）：{channel_id, invitee_id}，频道主/副频道主可发起"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传入信息错误", data=None)
        channel_id = data.get('channel_id')
        invitee_id = data.get('invitee_id')
        if not channel_id or not invitee_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 channel_id 或 invitee_id", data=None)
        invitee_id = int(invitee_id)
        me = g.user.id
        if invitee_id == me:
            return resp_json(code=PARAMS_ERROR, msg="不能邀请自己", data=None)
        channel = Channel.query.filter_by(id=channel_id).first()
        if not channel or channel.status != 1:
            return resp_json(code=PARAMS_ERROR, msg="频道不存在或已失效", data=None)
        if not check_channel_member_role(channel_id):
            return resp_json(code=PARAMS_ERROR, msg="只有频道主/副频道主可以邀请", data=None)
        invitee = User.query.filter_by(id=invitee_id, status=1).first()
        if invitee is None or invitee.deleted_at is not None:
            return resp_json(code=PARAMS_ERROR, msg="被邀请用户不存在", data=None)
        if ChannelMember.query.filter_by(channel_id=channel_id, user_id=invitee_id).first():
            return resp_json(code=PARAMS_ERROR, msg="对方已订阅该频道", data=None)
        if ChannelInvite.query.filter_by(channel_id=channel_id, invitee_id=invitee_id, status=0).first():
            return resp_json(code=PARAMS_ERROR, msg="已发送邀请，等待对方处理", data=None)
        invite = ChannelInvite(channel_id=channel_id, inviter_id=me, invitee_id=invitee_id, status=0)
        db.session.add(invite)
        db.session.flush()
        create_notification(invitee_id, 'channel_invite', title='频道邀请',
                            content=f"{g.user.nickname} 邀请你订阅「{channel.name}」",
                            related_id=invite.id, related_type='channel_invite')
        db.session.commit()
        socketio.emit('notification', {
            'type': 'channel_invite', 'title': '频道邀请',
            'content': f"{g.user.nickname} 邀请你订阅「{channel.name}」",
            'related_id': invite.id, 'related_type': 'channel_invite'
        }, room=str(invitee_id))
        return resp_json(msg="邀请已发送", data={"invite_id": invite.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/invites', methods=['GET'])
@login_required
def list_channel_invites():
    """我收到的待处理频道邀请"""
    try:
        me = g.user.id
        invites = ChannelInvite.query.filter_by(invitee_id=me, status=0) \
            .order_by(ChannelInvite.created_at.desc()).all()
        channel_ids = [i.channel_id for i in invites]
        inviter_ids = [i.inviter_id for i in invites]
        channels = {c.id: c for c in Channel.query.filter(Channel.id.in_(channel_ids)).all()} if channel_ids else {}
        inviters = {u.id: u for u in User.query.filter(User.id.in_(inviter_ids)).all()} if inviter_ids else {}
        result = []
        for i in invites:
            ch = channels.get(i.channel_id)
            inv = inviters.get(i.inviter_id)
            result.append({
                "invite_id": i.id,
                "channel_id": i.channel_id,
                "channel_name": ch.name if ch else "未知频道",
                "channel_avatar": ch.avatar if ch else None,
                "inviter_id": i.inviter_id,
                "inviter_nickname": inv.nickname if inv else f"用户 {i.inviter_id}",
                "created_at": i.created_at.isoformat() if i.created_at else None,
            })
        return resp_json(msg="获取成功", data={"invites": result, "total": len(result)})
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/invite/accept', methods=['POST'])
@login_required
def accept_channel_invite():
    """接受频道邀请：{invite_id} → 正式订阅"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传入信息错误", data=None)
        invite_id = data.get('invite_id')
        if not invite_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 invite_id", data=None)
        invite = ChannelInvite.query.get(invite_id)
        if not invite or invite.invitee_id != g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="邀请不存在或无权处理", data=None)
        if invite.status != 0:
            return resp_json(code=PARAMS_ERROR, msg="该邀请已被处理", data=None)
        channel = Channel.query.filter_by(id=invite.channel_id).first()
        if not channel or channel.status != 1:
            invite.status = 2
            invite.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(code=PARAMS_ERROR, msg="频道已不存在或已失效", data=None)
        if ChannelMember.query.filter_by(channel_id=channel.id, user_id=g.user.id).first():
            invite.status = 1
            invite.handled_at = _utcnow_naive()
            db.session.commit()
            return resp_json(msg="你已订阅该频道", data={"channel_id": channel.id, "already": True})
        db.session.add(ChannelMember(channel_id=channel.id, user_id=g.user.id, role=(2 if channel.default_vice_owner == 1 else 3), notify_level=1))
        upsert_conversation(g.user.id, 'channel', channel.id, None, '已加入频道')
        invite.status = 1
        invite.handled_at = _utcnow_naive()
        db.session.commit()
        inviter = User.query.get(invite.inviter_id)
        if inviter:
            create_notification(inviter.id, 'channel_invite', title='频道邀请已接受',
                                content=f"{g.user.nickname} 接受了你的频道邀请",
                                related_id=invite.id, related_type='channel_invite')
            socketio.emit('notification', {
                'type': 'channel_invite', 'title': '频道邀请已接受',
                'content': f"{g.user.nickname} 接受了你的频道邀请",
                'related_id': invite.id, 'related_type': 'channel_invite'
            }, room=str(inviter.id))
            db.session.commit()
        return resp_json(msg="已订阅频道", data={"channel_id": channel.id, "channel_name": channel.name, "channel_avatar": channel.avatar})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)


@bp.route('/invite/reject', methods=['POST'])
@login_required
def reject_channel_invite():
    """拒绝频道邀请：{invite_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(code=PARAMS_ERROR, msg="前端传入信息错误", data=None)
        invite_id = data.get('invite_id')
        if not invite_id:
            return resp_json(code=PARAMS_ERROR, msg="缺少 invite_id", data=None)
        invite = ChannelInvite.query.get(invite_id)
        if not invite or invite.invitee_id != g.user.id:
            return resp_json(code=PARAMS_ERROR, msg="邀请不存在或无权处理", data=None)
        if invite.status != 0:
            return resp_json(code=PARAMS_ERROR, msg="该邀请已被处理", data=None)
        invite.status = 2
        invite.handled_at = _utcnow_naive()
        db.session.commit()
        return resp_json(msg="已拒绝邀请", data={"invite_id": invite.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误,{str(e)}", data=None)
