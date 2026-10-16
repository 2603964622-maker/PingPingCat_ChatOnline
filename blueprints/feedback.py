import json

from flask import Blueprint, request, g

from decorators import login_required
from exts import db
from models.feedback import Feedback
from utils import resp_json, OK, PARAMS_ERROR, SERVER_ERROR, save_upload_file, get_file_size

bp = Blueprint('feedback', __name__, url_prefix='/feedback')

# 反馈分类：想法 / 建议 / 问题 / 其他（非法值统一归为 idea）
CATEGORIES = ('idea', 'suggestion', 'bug', 'other')
MAX_IMAGES = 9
MAX_CONTENT = 2000


def _item(f, user=None):
    """序列化一条反馈；user 为可选的提交人对象（管理端列表传入，避免定义模型关系）。"""
    try:
        images = json.loads(f.images) if f.images else []
    except Exception:
        images = []
    d = {
        "id": f.id,
        "category": f.category or 'idea',
        "content": f.content,
        "images": images,
        "status": f.status if f.status is not None else 0,
        "reply": f.reply,
        "replied_at": f.replied_at.isoformat() if f.replied_at else None,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }
    if user is not None:
        d["user"] = {"id": user.id, "nickname": user.nickname, "email": user.email, "number": user.number}
    return d


@bp.route('/submit', methods=['POST'])
@login_required
def submit():
    """提交想法/反馈：{category: idea|suggestion|bug|other, content(必填), images:[url,...](可选)}"""
    try:
        data = request.get_json(silent=True) or {}
        category = (data.get('category') or '').strip()
        if category not in CATEGORIES:
            category = 'idea'
        content = (data.get('content') or '').strip()
        if not content:
            return resp_json(PARAMS_ERROR, "内容不能为空", data=None)
        if len(content) > MAX_CONTENT:
            content = content[:MAX_CONTENT]

        images = data.get('images') or []
        if not isinstance(images, list):
            images = []
        images = [str(x) for x in images if isinstance(x, str) and x.strip()][:MAX_IMAGES]

        fb = Feedback(user_id=g.user.id, category=category, content=content,
                      images=json.dumps(images, ensure_ascii=False) if images else None)
        db.session.add(fb)
        db.session.commit()
        return resp_json(OK, "已收到，感谢你的想法", data={"feedback_id": fb.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/upload', methods=['POST'])
@login_required
def upload():
    """反馈配图上传：form-data file → {url, file_type, filename}（仅接受图片）"""
    try:
        f = request.files.get('file')
        if not f or not f.filename:
            return resp_json(PARAMS_ERROR, "缺少文件", data=None)
        if get_file_size(f) is None:
            return resp_json(PARAMS_ERROR, "文件过大（上限 100MB）", data=None)
        saved = save_upload_file(f, scope=('feedback', g.user.id))
        if not saved:
            return resp_json(PARAMS_ERROR, "文件保存失败，请更换格式重试", data=None)
        url, file_type, filename = saved
        if file_type != 3:
            return resp_json(PARAMS_ERROR, "反馈只支持上传图片", data=None)
        db.session.commit()
        return resp_json(OK, "上传成功", data={"url": url, "file_type": file_type, "filename": filename})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/mine', methods=['GET'])
@login_required
def mine():
    """我提交的想法列表：?limit=20&offset=0"""
    try:
        limit = min(int(request.args.get('limit', 20) or 20), 50)
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        q = Feedback.query.filter_by(user_id=g.user.id)
        total = q.count()
        rows = q.order_by(Feedback.id.desc()).offset(offset).limit(limit).all()
        return resp_json(OK, "获取成功", data={
            "feedbacks": [_item(x) for x in rows],
            "total": total,
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)
