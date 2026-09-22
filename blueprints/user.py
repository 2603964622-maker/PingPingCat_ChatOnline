from decorators import login_required
from exts import db,cache
from flask import Blueprint, request, jsonify, session, render_template,current_app,g
from models.user import User
import random
from utils import generate_unique_number

bp = Blueprint('user',__name__,url_prefix='/user')

@bp.route('/register',methods=['POST','GET'])
def register():
    try:
        if request.method =="GET":
            return render_template("register.html")
        data = request.get_json()
        email = data.get('email')
        code = data.get('email_code')
        mail_code = cache.get(f"res:{email}")  #cache.get()直接填写键值，不需要加引号 .cache.get("email")❌
        if code != mail_code:
            return jsonify(
                {
                    "code":400,
                    "msg":"邮箱验证码错误"
                }
            ),400
        number = generate_unique_number(type="user_")
        nickname = data.get('nickname')
        avatar_url = f"https://robohash.org/{email}?set=set4&size=200x200"
        password = data.get('password')
        user = User(number,email,nickname,avatar_url,password)
        db.session.add(user)
        db.session.commit()
        return jsonify({
            "code":0,
            "msg":"注册成功",
            "data":{
                "number":number,
                "email":email,
                "nickname":nickname,
                "avatar":avatar_url
            }
        }),200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code":500,
            "msg":f"注册失败:{str(e)}",
            "data":{None}
        }),500

@bp.route('/login',methods=['POST','GET'])
def login():
    try:
        if request.method =="GET":
            return render_template("login.html")
        data = request.get_json()
        if not data:
            return jsonify({"msg":"请求数据为空"}),400
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({"msg":"请输入邮箱或密码"}),400
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            return jsonify({"msg":"用户不存在或者密码错误"}),401
        session['user_id'] = user.id
        return jsonify({
            "msg":"登录成功",
            "code":0,
            "data":{
                "id": user.id,
                "number": user.number,
                "email": user.email,
                "nickname": user.nickname,
                "avatar": user.avatar,
                "status": user.status,
                "signature":user.Signature
            }
            }),200
    except Exception as e:
        return jsonify({"msg":f"服务器内部错误:{str(e)}"}),500

@bp.route('/logout')
def logout():
    session.clear()
    return jsonify({"msg":"登出成功"}),200

@bp.route("/mail/register/captcha",methods=['POST','GET'])
def register_mail_captcha():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "code":400,
                "msg":"请求格式错误，需要JSON"
            }),400
        email = data.get('email')
        digits = ["0","1","2","3","4","5","6","7","8","9"]
        captcha = "".join(random.sample(digits,4))
        body = f"【Ping】您正在注册账号,验证码是{captcha},5分钟内有效"
        current_app.celery.send_task("send_mail",(email,"Chatonline",body))
        cache.set(f"res:{email}",captcha,timeout=300)  # 验证码 和 邮箱 关联，设置关联时间300s，也就是5min
        return jsonify({
            "code":0,
            "msg":"验证码发送成功",
            "data":None
        }),200
    except Exception as e:
        return jsonify({
            "code":500,
            "msg":f"生成验证码错误:{str(e)}",
            "data":None
        }),500
@bp.route('/mail/update_passwd/captcha',methods=['POST'])
@login_required
def update_passwd_mail_captcha():
    try:
        email = g.user.email
        digits = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
        captcha = "".join(random.sample(digits, 4))
        body = f"【Ping】您正在修改密码,验证码是{captcha},请确保您是本人,验证码5分钟内有效"
        current_app.celery.send_task("send_mail", (email, "Chatonline", body))
        cache.set(f"pwd:{email}", captcha, timeout=300)  # 验证码 和 邮箱 关联，设置关联时间300s，也就是5min
        return jsonify({
            "code": 0,
            "msg": "验证码发送成功",
            "data": None
        }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"生成验证码错误:{str(e)}",
            "data": None
        }), 500

@bp.route('/update/password', methods=['POST', 'GET'])
@login_required
def update_password():
    try:
        if request.method == "GET":
            return render_template('update_passwd.html')
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "code": 400,
                "msg": "请求格式错误，需要JSON",
                "data": None
            }), 400
        email = g.user.email
        eCode = data.get('eCode')
        new_password = data.get('new_password')
        captcha = cache.get(f"pwd:{email}")
        if not captcha or eCode != captcha:
            if captcha:
                cache.delete(f"pwd:{email}")
            return jsonify({
                "code": 400,
                "msg": "验证码过期或不存在，请重新获取",
                "data": None
            }), 400
        cache.delete(f"pwd:{email}")
        user = User.query.filter_by(id=g.user.id).first()
        user.password = new_password
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "密码修改成功",
            "data": None
        }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"服务器内部错误{str(e)}，请稍后重试",
            "data": None
        }), 500

@bp.route('/update/personal_info', methods=['POST', 'GET'])
@login_required
def update_personal_info():
    try:
        if request.method == "GET":
            return render_template('personal_info.html')
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "code": 400,
                "msg": "请求格式错误，需要JSON"
            }), 400
        nickname = data.get('nickname')
        signature = data.get('signature')
        user = g.user
        if nickname is not None:
            user.nickname = nickname
        if signature is not None:
            user.Signature = signature
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg":"修改信息成功",
            "data":{
                "id": user.id,
                "number": user.number,
                "email": user.email,
                "nickname": user.nickname,
                "avatar": user.avatar,
                "status": user.status,
                "signature": user.Signature
            }
        }),200
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": f"服务器内部错误{str(e)}，请稍后重试",
            "data": None
        }), 500