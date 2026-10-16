import json

from flask import Blueprint, request, g

from decorators import login_required
from exts import db
from models.admin import Admin
from models.announcement import SiteAnnouncement, SiteAnnouncementRead
from utils import resp_json, OK, PARAMS_ERROR, SERVER_ERROR

bp = Blueprint('announcement', __name__, url_prefix='/announcement')


def _media_of(a):
    try:
        media = json.loads(a.media) if a.media else []
    except Exception:
        media = []
    return [m for m in media if isinstance(m, dict) and m.get('url')]


def _item(a, is_read=None, with_publisher=False):
    d = {
        "id": a.id,
        "title": a.title,
        "content": a.content,
        "media": _media_of(a),
        "status": a.status if a.status is not None else 1,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
    if is_read is not None:
        d["is_read"] = 1 if is_read else 0
    if with_publisher:
        pub = Admin.query.get(a.publisher_id) if a.publisher_id else None
        d["publisher"] = (pub.nickname or pub.username) if pub else None
    return d


def _pending_rows(uid):
    """我尚未“已知晓”的在投公告（新→旧）。"""
    read_ids = [r.announcement_id for r in SiteAnnouncementRead.query.filter_by(user_id=uid).all()]
    q = SiteAnnouncement.query.filter_by(status=1)
    if read_ids:
        q = q.filter(SiteAnnouncement.id.notin_(read_ids))
    return q.order_by(SiteAnnouncement.id.desc()).limit(20).all()


@bp.route('/pending', methods=['GET'])
@login_required
def pending():
    """当前用户未确认的公告：前端据此决定是否放出送信小狗"""
    try:
        rows = _pending_rows(g.user.id)
        return resp_json(OK, "获取成功", data={"announcements": [_item(a) for a in rows]})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/ack', methods=['POST'])
@login_required
def ack():
    """标记已知晓：{ids:[...]} 指定条；缺省/为空则把当前所有未读在投公告一并确认"""
    try:
        data = request.get_json(silent=True) or {}
        ids = data.get('ids')
        if isinstance(ids, list) and ids:
            ids = [int(x) for x in ids if str(x).strip().isdigit()]
            rows = SiteAnnouncement.query.filter(
                SiteAnnouncement.id.in_(ids), SiteAnnouncement.status == 1).all()
        else:
            rows = _pending_rows(g.user.id)
        acked = set(r.announcement_id for r in
                    SiteAnnouncementRead.query.filter_by(user_id=g.user.id).all())
        added = 0
        for a in rows:
            if a.id in acked:
                continue
            db.session.add(SiteAnnouncementRead(user_id=g.user.id, announcement_id=a.id))
            added += 1
        db.session.commit()
        return resp_json(OK, "已确认", data={"count": added})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/list', methods=['GET'])
@login_required
def list_announcements():
    """公告历史（含本人是否已知晓）：?limit=20&offset=0"""
    try:
        limit = min(int(request.args.get('limit', 20) or 20), 50)
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        rows = SiteAnnouncement.query.filter_by(status=1).order_by(
            SiteAnnouncement.id.desc()).offset(offset).limit(limit).all()
        total = SiteAnnouncement.query.filter_by(status=1).count()
        read_ids = set(r.announcement_id for r in
                       SiteAnnouncementRead.query.filter_by(user_id=g.user.id).all())
        return resp_json(OK, "获取成功", data={
            "announcements": [_item(a, is_read=(a.id in read_ids)) for a in rows],
            "total": total,
        })
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)
