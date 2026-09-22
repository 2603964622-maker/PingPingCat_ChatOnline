from flask import Blueprint,render_template,request,redirect,url_for,jsonify,g
from decorators import login_required
from models.channel import Channel,ChannelMember
from utils import generate_unique_number
from exts import db

bp = Blueprint('channels',__name__,url_prefix='/channel')

@bp.route('/create', methods=['POST'])
@login_required
def create_channel():
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



