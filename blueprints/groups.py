from flask import Blueprint,request,jsonify,g
from decorators import login_required
from models.group import GroupMessage, GroupAnnouncement,Group,GroupMember,AnnouncementUserIsRead
from utils import generate_unique_number, resp_json, save_upload_file
from models.upload import GroupFile
from models.user import User
from exts import db

#导入参数
from utils import PARAMS_ERROR,OK,SERVER_ERROR
#--------------------------------------------------------------------------
def select_member_role(group_id):
    member = GroupMember.query.filter_by(user_id=g.user.id,group_id=group_id).first()
    role = member.role
    return role
#--------------------------------------------------------------------------
# 身份/权限类（当前用户是谁、有没有权限）→ 永远用 g.user，不信任前端传的 user_id
# 业务/资源类（操作哪个群、帖子、目标用户、消息内容）→ 前端传参，明确告诉后端操作对象
#--------------------------------------------------------------------------
bp = Blueprint('groups',__name__,url_prefix='/group')

@bp.route('/create', methods=['POST'])
@login_required
def create_group():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求格式错误，需要JSON", "data": None}), 400
        owner_id = g.user.id
        name = data.get('group_name')
        if not name or not name.strip():  #  .strip():去掉字符串首位的空白   这里的意思是判断name是否权威空白
            return jsonify({"code": 400, "msg": "群组名称不能为空", "data": None}), 400
        number = generate_unique_number(type="group_")
        name = name.strip()     # 去除空白
        description = data.get('group_desc')
        max_members = data.get('max_members', 200)
        avatar_url = f"https://robohash.org/{number}?set=set2&size=200×200"
        group = Group(owner_id=owner_id,number=number,name=name,description=description,max_members=max_members,avatar=avatar_url)
        db.session.add(group)
        db.session.flush()
        # 群主加入成员表
        member = GroupMember(group_id=group.id,user_id=owner_id,role=1,nickname=None)
        db.session.add(member)
        db.session.commit()

        return jsonify({
            "code": 200,
            "msg": "创建群组成功",
            "data": {
                "id": group.id,
                "owner_id": owner_id,
                "number": number,
                "name": name,
                "description": group.description,
                "avatar": avatar_url,
                "max_members": group.max_members,
                "status": group.status
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": f"服务器内部出错: {str(e)}",
            "data": None
        }), 500
@bp.route('/join', methods=['POST'])
@login_required
def join_group():
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="前端传入信息错误",data=None)
        group_id = data.get('group_id')
        user_id = g.user.id
        nickname = g.user.nickname
        if not group_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少群号",data=None)
        if not Group.query.filter_by(group_id=group_id).first():
            return resp_json(code=PARAMS_ERROR,msg="该群聊不存在",data=None)
        if GroupMember.query.filter_by(user_id=user_id,group_id=group_id).first():
            return resp_json(code=PARAMS_ERROR,msg="您已在群聊中，请勿重复加入",data=None)
        group_member = GroupMember(group_id=group_id,user_id=user_id,nickname=nickname)
        db.session.add(group_member)
        db.session.commit()
        return resp_json(msg="加入群聊成功",data={
            "group_id": group_id,
            "user_id": user_id,
            "nickname": nickname
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误: {str(e)}",data=None)

@bp.route('/leave', methods=['POST'])
@login_required
def leave_group():
    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="前端传递信息错误",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少群组信息",data=None)
        user = Group.query.filter_by(group_id=group_id,user_id=g.user.id).first()
        if not user:
            return resp_json(code=PARAMS_ERROR,msg="不存在此群组，无法退出",data=None)
        if user.role != 1:
            GroupMember.query.filter_by(user_id=user.id,group_id=group_id).delete()
            return resp_json(msg="退出群组成功",data={
                "group_id": group_id,
                "user_id": user.id
            })
        else:
            return resp_json(code=PARAMS_ERROR,msg="群主不可直接推出群聊,可解散/转让",data=None)
    except Exception as e:
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误{str(e)}",data=None)

@bp.route('/owner/disband', methods=['POST'])
@login_required
def disband_group():

    try:
        data = request.get_json()
        if not data:
            return resp_json(code=PARAMS_ERROR,msg="前端传递信息错误",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(code=PARAMS_ERROR,msg="缺少群组编号",data=None)
        member = GroupMember.query.filter_by(user_id=g.user.id,group_id=group_id).first()
        if member.role != 1:
            return resp_json(code=PARAMS_ERROR,msg="仅限管理员操作",data=None)
        else:
            Group.query.filter_by(group_id=group_id,owner_id=g.user.id).delete()
            GroupMember.query.filter_by(group_id=group_id).delete()
            GroupMessage.query.filter_by(group_id=group_id).delete()
            GroupAnnouncement.query.filter_by(group_id=group_id).delete()
            GroupFile.query.filter_by(group_id=group_id).delete()
        return resp_json(msg="解散群聊成功")
    except Exception as e:
        return resp_json(code=SERVER_ERROR,msg=f"服务器内部错误{str(e)}",data=None)

@bp.route('/owner/owned', methods=['POST'])
@login_required
def owned_group():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端数据传输错误",data=None)
        group_id = data.get('group_id')
        to_owner_id = data.get('to_owner_id')
        role = select_member_role(group_id)
        if role != 1:
            return resp_json(PARAMS_ERROR,"用户非群主，无法移交",data=None)
        to_owner_user = GroupMember.query.filter_by(id=to_owner_id).first()
        if not to_owner_user:
            return resp_json(PARAMS_ERROR,"该用户不在组内",data=None)
        before_owner_user = GroupMember.query.filter_by(id=g.user.id,group_id=group_id).first()
        after_owner_user = GroupMember.query.filter_by(id=to_owner_user.id,group_id=group_id).first()
        before_owner_user.role = 3
        after_owner_user.role = 1
        db.session.commit()
    except Exception as e:
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)

@bp.route('/publish/file', methods=['POST'])
@login_required
def group_publish_file():
    try:
        files = request.files.getlist("file")  # ← 关键：getlist , 获取多个文件
        if not files or len(files) == 0:
            return resp_json(PARAMS_ERROR, "所上传文件为空", data=None)

        group_id = request.form.get("group_id")
        if not group_id:
            return resp_json(PARAMS_ERROR, "缺失群组编号", data=None)
        results = []
        for file in files:
            if not file.filename:  # 跳过空文件项
                continue
            file_url, file_type, filename = save_upload_file(file)
            group_file = GroupFile(
                group_id=group_id,
                file_url=file_url,
                file_type=file_type,
                filename=filename,
                sender_id=g.user.id
            )
            db.session.add(group_file)
            results.append({
                "file_url": file_url,
                "file_type": file_type,
                "filename": filename
            })
        db.session.commit()
        return resp_json("文件上传成功", data={
            "group_id": group_id,
            "files": results,
            "count": len(results)
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误{str(e)}", data=None)

@bp.route('/permission/set', methods=['POST'])
@login_required
def group_permission_set():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端传递参数错误",data=None)
        group_id = data.get('group_id')
        action = data.get('action')
        if not group_id:
            return resp_json(PARAMS_ERROR,"缺少群组编号",data=None)
        target_member_id = data.get('target_member_id')
        if target_member_id == g.user.id:
            return resp_json(PARAMS_ERROR,"群主不可以对自己操作")
        if not target_member_id:
            return resp_json(PARAMS_ERROR,"缺少群成员编号",data=None)
        target_member = GroupMember.query.filter_by(user_id=target_member_id,group_id=group_id).first()
        if not target_member:
            return resp_json(PARAMS_ERROR,"非群成员",data=None)
        if action == "promote":
            if target_member.role == 2:
                return resp_json(PARAMS_ERROR,"此用户已是管理员",data=None)
            target_member.role = 2
            msg="设置管理员成功"
        else:
            if target_member.role != 2:
                return resp_json(PARAMS_ERROR,"该用户已经为普通群成员",data=None)
            target_member.role = 3
            msg ="管理员降级成功"
        db.session.commit()
        return resp_json(msg=msg, data={
            "target_member_id": target_member_id,
            "action": action,
            "role": target_member.role
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)
@bp.route('/publish/announcement', methods=['POST'])
@login_required
def publish_group_announcement():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端传入信息错误",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(PARAMS_ERROR,"缺少群组信息",data=None)
        content = data.get('content')
        if not content:
            return resp_json(PARAMS_ERROR,"缺少公告信息",data=None)
        user_role = select_member_role(group_id)
        if user_role == 3:
            return resp_json(PARAMS_ERROR,"普通用户无法发布公告",data=None)
        announcement = GroupAnnouncement(group_id=group_id,publisher_id=g.user.id,content=content)
        db.session.add(announcement)
        db.session.flush()
        anno_id = announcement.id
        anno_is_read = AnnouncementUserIsRead(user_id=g.user.id,announcement_id=anno_id,group_id=group_id)
        db.session.add(anno_is_read)
        db.session.commit()
        return resp_json("发布公告成功", data={
            "announcement_id": anno_id,
            "group_id": group_id,
            "content": content,
            "publisher_id": g.user.id
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)

@bp.route('/announcement/get', methods=['POST'])
@login_required
def read_group_announcement():
    try:
        data = request.get_json()
        if not data:
            return resp_json(PARAMS_ERROR,"前端数据传输为空",data=None)
        group_id = data.get('group_id')
        if not group_id:
            return resp_json(PARAMS_ERROR,"缺少群组信息",data=None)
        announcement_id = data.get('announcement_id')
        if not announcement_id:
            return resp_json(PARAMS_ERROR,"缺少公告编号",data=None)
        if AnnouncementUserIsRead.query.filter_by(announcement_id=announcement_id,user_id=g.user.id).first():
            return resp_json(PARAMS_ERROR,"用户信息已存储在表中",data=None)
        anno = AnnouncementUserIsRead(user_id=g.user.id,announcement_id=announcement_id,group_id=group_id)
        db.session.add(anno)
        db.session.commit()
        return resp_json("插入公告已读表成功", data={
            "announcement_id": anno.id,
            "group_id": group_id,
            "user_id": g.user.id
        })
    except Exception as e:
        return resp_json(SERVER_ERROR,f"服务器内部错误,{str(e)}",data=None)


@bp.route('/group/<int:group_id>/messages', methods=['GET'])
@login_required
def get_group_messages(group_id):
    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")

    before_id = request.args.get('before_id', type=int)  #before_id 就是一个"我当前看到的最早一条消息的 id"，告诉后端："别再给我这条和之后的了，给我这条之前的。"
    per_page = request.args.get('per_page', 20, type=int)  # 每次加载消息的数量,20为默认值，如果前端没有传递per_page默认就用20

    query = GroupMessage.query.filter_by(group_id=group_id)
    if before_id:
        query = query.filter(GroupMessage.id < before_id)  #把前端传递的before_id 和 消息id 做对比，返回比before_id 编号小的Query 对象

    messages = query.order_by(GroupMessage.id.desc()).limit(per_page).all()
#   降序查询GroupMessage的id,然后只查询20条，然后返回
    messages.reverse()  # Python列表方法，反向输出
    msg_list = []
    for m in messages:
        msg_list.append({
            "msg_id": m.id,
            "sender_id": m.sender_id,
            "sender_name": m.sender.nickname if m.sender else "未知用户",
            "content": "[已撤回]" if m.deleted_at else m.content,
            "msg_type": m.msg_type,
            "created_at": m.created_at.isoformat() if m.created_at else None
        })

    return resp_json(OK, "获取成功", data={
        "messages": msg_list,
        "has_more": len(messages) == per_page
    })

@bp.route('/group/<int:group_id>/members', methods=['GET'])
@login_required
def get_group_members(group_id):
    # 1. 校验当前用户是不是群成员
    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")

    # 2. 查该群所有成员
    members = GroupMember.query.filter_by(group_id=group_id).all()

    # 3. 拼数据
    member_list = []
    for m in members:
        user = User.query.get(m.user_id) #ORM模型.query.get(主键):  按主键查一条，直接返回对象
        if not user:
            continue
        member_list.append({
            "user_id": user.id,
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
            "role": m.role,          # 1=群主 2=管理员 3=普通成员
            "role_name": {1: "群主", 2: "管理员", 3: "成员"}.get(m.role, "成员"),
            "joined_at": m.joined_at.isoformat() if hasattr(m, 'joined_at') and m.joined_at else None
        })

    return resp_json(OK, "获取成功", data={
        "members": member_list,
        "total": len(member_list)
    })
@bp.route('/group/info', methods=['PUT'])
@login_required
def update_group_info():
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return resp_json(PARAMS_ERROR, "group_id 不能为空")

    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")
    if member.role not in (1, 2):
        return resp_json(PARAMS_ERROR, "只有群主或管理员能修改群信息")

    group = Group.query.get(group_id)
    if not group:
        return resp_json(PARAMS_ERROR, "群不存在")

    data = request.get_json()
    if not data:
        return resp_json(PARAMS_ERROR, "请求体不能为空")

    changed = False

    name = data.get('name')
    if name is not None:
        name = name.strip()
        if not name or len(name) > 50:
            return resp_json(PARAMS_ERROR, "群名称长度需在1-50之间")
        group.name = name
        changed = True

    avatar_url = data.get('avatar_url')
    if avatar_url is not None:
        group.avatar_url = avatar_url.strip()
        changed = True

    description = data.get('description')
    if description is not None:
        if len(description) > 200:
            return resp_json(PARAMS_ERROR, "简介不能超过200字")
        group.description = description
        changed = True

    if not changed:
        return resp_json(PARAMS_ERROR, "没有可修改的字段")

    db.session.commit()

    return resp_json(OK, "修改成功", data={
        "group_id": group.id,
        "name": group.name,
        "avatar_url": group.avatar_url,
        "description": group.description
    })
@bp.route('/group/transfer', methods=['PUT'])
@login_required
def transfer_group_owner():
    """
    群主转让
    :return:
    """
    # 1. group_id 从 URL Query 取
    group_id = request.args.get('group_id', type=int)
    if not group_id:
        return resp_json(PARAMS_ERROR, "group_id 不能为空")

    # 2. 校验当前用户是群主
    member = GroupMember.query.filter_by(group_id=group_id, user_id=g.user.id).first()
    if not member:
        return resp_json(PARAMS_ERROR, "你不是群成员")
    if member.role != 1:
        return resp_json(PARAMS_ERROR, "只有群主才能转让群")

    # 3. new_owner_id 从 JSON body 取
    data = request.get_json()
    if not data:
        return resp_json(PARAMS_ERROR, "请求体不能为空")

    new_owner_id = data.get('new_owner_id')
    if not new_owner_id:
        return resp_json(PARAMS_ERROR, "new_owner_id 不能为空")

    # 4. 校验目标用户是群成员
    target = GroupMember.query.filter_by(group_id=group_id, user_id=new_owner_id).first()
    if not target:
        return resp_json(PARAMS_ERROR, "对方不是群成员")

    # 不能转给自己
    if new_owner_id == g.user.id:
        return resp_json(PARAMS_ERROR, "不能转给自己")

    # 5. 转让：原群主降为普通成员(role=3)，新群主升为 role=1
    member.role = 3
    target.role = 1

    db.session.commit()

    return resp_json(OK, "群主转让成功", data={
        "group_id": group_id,
        "old_owner_id": g.user.id,
        "new_owner_id": new_owner_id
    })