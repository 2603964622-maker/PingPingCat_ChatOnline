from flask import render_template,Blueprint,g,request,jsonify,redirect,url_for
from decorators import login_required
from models.user import User
from models.group import Group
from models.channel import Channel
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
            to_user_id = user.id
            return jsonify({
                "code":0,
                "msg":"查询用户成功",
                "data":{
                    "to_user_id":to_user_id
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
                    "to_group_id":to_group_id
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
                     "to_channel_id": to_channel_id
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


#TODO 搜索内容【基于用户加入的频道、群聊、用户聊天记录】
@bp.route('/content',methods=['GET','POST'])
@login_required
def search_content():
    if request.method == 'GET':
        return render_template('content_search.html')
    pass