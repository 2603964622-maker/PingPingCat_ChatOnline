from flask import Blueprint,render_template,request,jsonify,session,g
from exts import db
from models.user import User
from models.upload import UploadFile
from utils import save_upload_file,delete_old_file,get_file_size,generate_filename
from decorators import login_required
bp = Blueprint('upload',__name__,url_prefix='/upload')

#TODO : 生产环境中 记得把上传文件改为对象存储，开发环境先存储到项目文件夹中的/static中

@bp.route('/avatar',methods=['POST'])
@login_required
def upload_avatar():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400

        file = request.files.get('file')
        delete_old_file(g.user.avatar)
        file_size = get_file_size(file)
        path = save_upload_file(file, category='avatar')
        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败", "data": None}), 400

        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 1
        upload = UploadFile(user_id=user_id,filename=filename,file_url=file_url,file_size=file_size,file_type=file_type)
        # 更新用户头像

        g.user.avatar = path
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "头像上传成功",
            "data": {
                "avatar_url": path
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500


@bp.route('/picture', methods=['POST'])
@login_required
def upload_picture():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400

        file = request.files['file']
        # 复用你的核心保存函数，category='picture' 会走图片白名单和 picture 目录
        file_size = get_file_size(file)
        path = save_upload_file(file, category='picture')

        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败", "data": None}), 400

        # 如果业务需要关联某条记录（如动态/消息），可在此处写数据库逻辑
        # 例如：new_img = Picture(url=path); db.session.add(new_img); db.session.commit()
        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 1
        upload = UploadFile(user_id=user_id, filename=filename, file_url=file_url, file_size=file_size,file_type=file_type)
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "图片上传成功",
            "data": {
                "url": path  # 返回 /static/picture/xxx.jpg
            }
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500


@bp.route('/media', methods=['POST'])
@login_required
def upload_media():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400

        file = request.files['file']
        # category='media' 会走视频白名单和 media 目录
        file_size = get_file_size(file)
        path = save_upload_file(file, category='media')

        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败(仅支持视频)", "data": None}), 400
        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 2
        upload = UploadFile(user_id=user_id, filename=filename, file_url=file_url, file_size=file_size,file_type=file_type)
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "视频上传成功",
            "data": {
                "url": path  # 返回 /static/media/xxx.mp4
            }
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500

@bp.route('/audio', methods=['POST'])
@login_required
def upload_audio():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400
        file = request.files['file']
        file_size = get_file_size(file)
        path = save_upload_file(file, category='audio')
        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败(仅支持音频)", "data": None}), 400
        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 3
        upload = UploadFile(user_id=user_id, filename=filename, file_url=file_url, file_size=file_size, file_type=file_type)
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "音频上传成功",
            "data": {
                "url": path
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500

@bp.route('/document', methods=['POST'])
@login_required
def upload_document():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400
        file = request.files['file']
        file_size = get_file_size(file)
        path = save_upload_file(file, category='document')
        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败(仅支持文档)", "data": None}), 400
        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 4
        upload = UploadFile(user_id=user_id, filename=filename, file_url=file_url, file_size=file_size, file_type=file_type)
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "文档上传成功",
            "data": {
                "url": path
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500

@bp.route('/compressed_file', methods=['POST'])
@login_required
def upload_compressed_file():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400
        file = request.files['file']
        file_size = get_file_size(file)
        path = save_upload_file(file, category='archive')
        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败(仅支持压缩文件)", "data": None}), 400
        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 5
        upload = UploadFile(user_id=user_id, filename=filename, file_url=file_url, file_size=file_size, file_type=file_type)
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "压缩文件上传成功",
            "data": {
                "url": path
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500
