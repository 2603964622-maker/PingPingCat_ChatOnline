import json

from flask import Blueprint, request, g

from decorators import login_required
from exts import db
from models.report import Report
from models.user import User
from models.group import Group
from models.channel import Channel
from utils import resp_json, OK, PARAMS_ERROR, SERVER_ERROR

bp = Blueprint('report', __name__, url_prefix='/report')

VALID_TARGET_TYPES = ('user', 'group', 'channel')


def _resolve_target(target_type, target_id):
    """校验被举报对象存在，返回 (存在?, 对象名快照)。"""
    if target_type == 'user':
        u = User.query.filter_by(id=target_id).first()
        return (u is not None and u.deleted_at is None), (u.nickname if u else None)
    if target_type == 'group':
        grp = Group.query.filter_by(id=target_id).first()
        return (grp is not None and grp.status == 1), (grp.name if grp else None)
    if target_type == 'channel':
        ch = Channel.query.filter_by(id=target_id).first()
        return (ch is not None and ch.status == 1), (ch.name if ch else None)
    return False, None


@bp.route('/create', methods=['POST'])
@login_required
def create_report():
    """提交举报：{target_type: user|group|channel, target_id, reason(必填), images:[url,...](可选)}"""
    try:
        data = request.get_json(silent=True) or {}
        target_type = (data.get('target_type') or '').strip()
        target_id = data.get('target_id')
        reason = (data.get('reason') or '').strip()
        images = data.get('images') or []

        if target_type not in VALID_TARGET_TYPES:
            return resp_json(PARAMS_ERROR, "举报对象类型非法（仅支持 user/group/channel）", data=None)
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return resp_json(PARAMS_ERROR, "缺少或非法的举报对象编号", data=None)
        if not reason:
            return resp_json(PARAMS_ERROR, "举报理由不能为空", data=None)
        if len(reason) > 500:
            reason = reason[:500]
        # 不能举报自己
        if target_type == 'user' and target_id == g.user.id:
            return resp_json(PARAMS_ERROR, "不能举报自己", data=None)

        exists, name = _resolve_target(target_type, target_id)
        if not exists:
            return resp_json(PARAMS_ERROR, "举报对象不存在或已失效", data=None)

        # 图片：仅保留字符串 URL，最多 9 张
        if not isinstance(images, list):
            images = []
        images = [str(x) for x in images if isinstance(x, str) and x.strip()][:9]
        images_json = json.dumps(images, ensure_ascii=False) if images else None

        # 同一举报人对同一对象，24 小时内已有待处理举报则合并（避免刷屏重复提交）
        dup = Report.query.filter(
            Report.reporter_id == g.user.id,
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == 0,
        ).order_by(Report.id.desc()).first()
        if dup:
            from datetime import datetime, timezone, timedelta
            ct = dup.created_at
            if ct and ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            if ct and datetime.now(timezone.utc) - ct < timedelta(hours=24):
                return resp_json(OK, "你已提交过该举报，我们会尽快处理", data={"report_id": dup.id, "merged": True})

        rep = Report(
            reporter_id=g.user.id, target_type=target_type, target_id=target_id,
            target_name=(name or '')[:128] or None, reason=reason, images=images_json,
        )
        db.session.add(rep)
        db.session.commit()
        return resp_json(OK, "举报已提交，感谢你的反馈", data={"report_id": rep.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/mine', methods=['GET'])
@login_required
def my_reports():
    """我提交的举报列表（供前端查看处理进度，可选）"""
    try:
        rows = Report.query.filter_by(reporter_id=g.user.id).order_by(Report.id.desc()).limit(50).all()
        return resp_json(OK, "获取成功", data={"reports": [{
            "id": r.id,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "target_name": r.target_name,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)
