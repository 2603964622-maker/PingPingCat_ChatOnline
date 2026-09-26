from flask import Blueprint,render_template,request,jsonify,session,g


from exts import db
from models.upload import AvatarFile
from utils import save_upload_file,delete_old_file,get_file_size,generate_filename,get_avatar_size,resp_json
from decorators import login_required
from utils import ALLOWED_IMAGE_EXTENSIONS,ALLOWED_VIDEO_EXTENSIONS
bp = Blueprint('upload',__name__,url_prefix='/upload')

#TODO : 生产环境中 记得把上传文件改为对象存储，开发环境先存储到项目文件夹中的/static中

@bp.route('/avatar',methods=['POST'])
@login_required
def upload_avatar():
    try:
        if 'file' not in request.files:
            return jsonify({"code": 400, "msg": "没有文件", "data": None}), 400
        file = request.files.get('file')
        avatar_size = get_avatar_size(file)
        if avatar_size is None:
            return jsonify({"code":400,"msg":"头像大小限制为2MB","data":None}), 400
        delete_old_file(g.user.avatar)
        path = save_upload_file(file, force_category='avatar')
        if not path:
            return jsonify({"code": 400, "msg": "文件格式不支持或上传失败", "data": None}), 400
        user_id = g.user.id
        filename = generate_filename(file.filename)
        file_url = path
        file_type = 1
        upload = AvatarFile(user_id=user_id,filename=filename,file_url=file_url,file_size=avatar_size,file_type=file_type)
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




