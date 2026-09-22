from flask import Blueprint,request,jsonify,g
from decorators import login_required
from utils import generate_unique_number
from models.group import Group,GroupMember
from exts import db

bp = Blueprint('groups',__name__,url_prefix='/group')
@bp.route('/create', methods=['POST'])
@login_required
def create_group():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"code": 400, "msg": "请求格式错误，需要JSON", "data": None}), 400

        owner_id = g.user.id
        number = generate_unique_number(type="group_")

        name = data.get('group_name')
        if not name or not name.strip():
            return jsonify({"code": 400, "msg": "群组名称不能为空", "data": None}), 400
        name = name.strip()

        description = data.get('group_desc')
        max_members = data.get('max_members', 200)
        avatar_url = f"https://robohash.org/{number}?set=set2&size=200×200"

        group = Group(
            owner_id=owner_id,
            number=number,
            name=name,
            description=description,
            max_members=max_members,
            avatar=avatar_url
        )
        db.session.add(group)
        db.session.flush()

        # 群主加入成员表
        member = GroupMember(
            group_id=group.id,
            user_id=owner_id,
            role=1,              # 1=群主（你的注释里写的）
            nickname=None       # 默认用原昵称
        )
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