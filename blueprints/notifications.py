from flask import Blueprint, request, g

from decorators import login_required
from exts import db
from models.notification import Notification
from utils import resp_json
from utils import OK, PARAMS_ERROR, SERVER_ERROR

bp = Blueprint('notifications', __name__, url_prefix='/notification')

# 通知类型 → 展示标签（前端列表用）
TYPE_LABELS = {
    'system': '系统',
    'mention': '群聊',
    'reply': '动态',
    'like': '动态',
    'post': '频道',
    'friend': '好友',
    'friend_request': '好友',
    'group_invite': '群聊',
    'group_owner_transfer': '群聊',
    'group_kick': '群聊',
    'group_mute': '群聊',
    'channel_invite': '频道',
}


def _item(n):
    label = TYPE_LABELS.get(n.type, '系统')
    # reply/like 同时用于“动态”与“频道帖子”的评论/回复，按 related_type 精确区分展示标签
    if n.related_type == 'channel_post':
        label = '频道'
    elif n.related_type == 'moment':
        label = '动态'
    return {
        "id": n.id,
        "type": n.type,
        "type_label": label,
        "title": n.title,
        "content": n.content,
        "related_id": n.related_id,
        "related_type": n.related_type,
        "is_read": n.is_read or 0,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@bp.route('/list', methods=['GET'])
@login_required
def list_notifications():
    """我的站内通知：?limit=30&offset=0&unread_only=0"""
    try:
        limit = min(int(request.args.get('limit', 30) or 30), 100)
        offset = max(int(request.args.get('offset', 0) or 0), 0)
        unread_only = request.args.get('unread_only', '0') in ('1', 'true', 'True')
        q = Notification.query.filter_by(user_id=g.user.id)
        if unread_only:
            q = q.filter(Notification.is_read == 0)
        total = q.count()
        items = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
        unread = Notification.query.filter_by(user_id=g.user.id, is_read=0).count()
        return resp_json(code=OK, msg="获取成功", data={
            "notifications": [_item(n) for n in items],
            "total": total,
            "unread": unread,
        })
    except Exception as e:
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


@bp.route('/read', methods=['POST'])
@login_required
def mark_read():
    """标记已读：{ids:[...]} 标记指定条；{all:1} 全部已读"""
    try:
        data = request.get_json(silent=True) or {}
        if data.get('all'):
            n = Notification.query.filter_by(user_id=g.user.id, is_read=0).update({'is_read': 1})
            db.session.commit()
            return resp_json(code=OK, msg="已全部标记为已读", data={"count": n})
        ids = data.get('ids')
        if not ids:
            return resp_json(code=PARAMS_ERROR, msg="缺少 ids", data=None)
        n = Notification.query.filter(Notification.user_id == g.user.id,
                                      Notification.id.in_(ids)).update({'is_read': 1},
                                                                      synchronize_session=False)
        db.session.commit()
        return resp_json(code=OK, msg="已读", data={"count": n})
    except Exception as e:
        db.session.rollback()
        return resp_json(code=SERVER_ERROR, msg=f"服务器内部错误：{str(e)}", data=None)


# 注：系统消息不提供对外的“发送”接口（避免任意用户给他人伪造通知），
# 各业务流程（转让/邀请/禁言等）在后端直接调用 utils.create_notification 写入。
