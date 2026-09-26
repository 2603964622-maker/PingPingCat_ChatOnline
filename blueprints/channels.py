from flask import Blueprint,render_template,request,redirect,url_for,jsonify,g,current_app
from sqlalchemy import true
import os
from decorators import login_required
from models.channel import Channel,ChannelMember,ChannelPost,ChannelLike,ChannelComment,ChannelAnnouncement
from models.upload import ChannelFile
from models.user import User
from utils import generate_unique_number, resp_json, save_upload_file,generate_filename,get_file_size
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
    if member_role.role == 3:
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
        avatar_url = f"https://robohash.org/{number}?set=set3&size=200×200"
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
        if not ChannelMember.query.filter_by(user_id=user_id,channel_id=channel_id).first():
            user_id = user_id
            role = 3
            notify_level = 1
            channel_member = ChannelMember(channel_id=channel_id,user_id=user_id,role=role,notify_level=notify_level)
            db.session.add(channel_member)
            db.session.commit()
            return jsonify({"code":0,"msg":"加入频道成功","data":{
                "channel_id":channel_id,
                "user_id":user_id,
                "role":role,
                "notify_level":notify_level
            }}),200
        else:
            return jsonify({"code": 400,"msg":"已加入频道，不可重复加入","data":None}), 400
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
        if ChannelMember.query.filter_by(user_id=user_id,channel_id=channel_id).first():
            will_delete_member = ChannelMember.query.filter_by(user_id=user_id,channel_id=channel_id).first()
            db.session.delete(will_delete_member)
            db.session.commit()
            return jsonify({"code":"0","msg":"退出频道成功","data":None}), 200
        else:
            return jsonify({"code":"400","msg":"不在频道无法退出","data":None}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"code":500,"msg":f"服务器错误,{str(e)}","data":None}), 500

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
        # 创建帖子
        post = ChannelPost(author_id=author_id, channel_id=channel_id, title=title, content=content)
        db.session.add(post)
        db.session.flush()
        post_id = post.id

        # 处理文件（支持多文件）
        files = request.files.getlist('file')
        file_results = []

        for file in files:
            if not file.filename:  # 跳过空文件项
                continue

            file_path, file_type, filename = save_upload_file(file)
            file_size = get_file_size(file)
            if file_size is None:
                return resp_json(code=PARAMS_ERROR, msg="文件超出大小限制", data=None)
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
        post_list = []
        for post in channel_post:
            if post.status != 1:
                continue
            post_data = {
                "post_id":post.id,
                "title":post.title,
                "content":post.content,
                "author_id":post.author_id,
                "view_count":post.view_count,
                "created_at": post.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }
            post_list.append(post_data)
        file = []
        for channel_files in channel_post.channel_files:
            channel_file ={
                "post_id":channel_files.post_id,
                "file_id":channel_files.file_id,
                "file_type":channel_files.file_type,
                "sender_id":channel_files.sender_id
            }
            file.append(channel_file)
        if not channel_post:
            return resp_json(code=PARAMS_ERROR,msg="没有任何帖子，发布一条吧", data=None)
        return resp_json(msg="获取帖子成功",data={"channel_post": post_list, "channel_file": file})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误,{str(e)}",data=None)

#TODO 频道主用户管理【删除用户；给用户设立角色】
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
        if not ChannelMember.query.filter_by(ChannelMember.user_id == delete_user_id).first():
            return resp_json(code=PARAMS_ERROR, msg='非频道用户', data=None)
        user = ChannelMember.query.filter_by(user_id=delete_user_id,channel_id=channel_id).first()
        db.session.delete(user)
        db.session.commit()
        return resp_json(msg="删除用户成功", data={"target_user_id": delete_user_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR,msg=f"数据库内部错误,{str(e)}",data=None)


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

        # ===== 1. 遍历频道文件，删除 ChannelFile 记录 + 磁盘文件 =====
        channel_files = ChannelFile.query.filter_by(channel_id=channel_id).all()
        for cf in channel_files:
                file_path = cf.file_url
                if os.path.exists(file_path):
                    os.remove(file_path)
        # ===== 2. 删除所有子表记录（顺序无所谓，只要都在删 Channel 之前）=====
        ChannelFile.query.filter_by(channel_id=channel_id).delete()
        ChannelLike.query.filter_by(channel_id=channel_id).delete()
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
        if not check_channel_member_is_boss(g.user.id):
            return resp_json(PARAMS_ERROR,"非频道主，无权执行此操作", data=None)
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

        # ===== 存评论 =====
        comment = ChannelComment(
            post_id=post_id,
            channel_id=channel_id,
            user_id=g.user.id,
            content=content.strip()
        )
        db.session.add(comment)
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
