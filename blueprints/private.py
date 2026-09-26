from flask import render_template,Blueprint,request,jsonify
from decorators import login_required
from models import User

bp = Blueprint('chat',__name__,url_prefix='/chat')



@bp.route('/to_user_id', methods=['GET'])
@login_required
def get_to_user_id_by_avatar():
    try:
        """
            这里超级不明白，不明白前端怎么传输的to_user_id【假设前端会传递吧】
            
        """
        data = request.get_json()
        to_user_id = int(data['to_user_id'])
        if not to_user_id:
            return jsonify({"code": 400, "msg": "缺少 user_id 参数", "data": None}), 400

        user = User.query.get(to_user_id)
        if not user:
            return jsonify({"code": 404, "msg": "该用户不存在", "data": None}), 404

        return jsonify({
            "code": 200,
            "msg": "获取成功",
            "data": {
                "to_user_id": user.id,
                "number": user.number,
                "nickname": user.nickname,
                "avatar": user.avatar
            }
        }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"服务器错误: {str(e)}",
            "data": None
        }), 500

