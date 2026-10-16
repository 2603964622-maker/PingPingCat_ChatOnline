"""Bot 配置与日志接口。
- 可启用的 bot 模板列表（供前端勾选）
- 群/频道维度的绑定/解绑/开关/参数配置/日志查询
权限：群聊仅群主/管理员(role 1/2)，频道仅频道主/副频道主(role 1/2)。
"""
import json

from flask import Blueprint, request, g
from decorators import login_required
from utils import resp_json, OK, PARAMS_ERROR, SERVER_ERROR
from exts import db
from models.bot import BotTemplate, GroupBot, ChannelBot, BotLog
from models.group import Group, GroupMember
from models.channel import Channel, ChannelMember
from models.user import User

bp = Blueprint('bot', __name__, url_prefix='/bot')


# ---------------------------------------------------------------- 通用工具
def _safe_json(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}


def _tpl_dict(t):
    return {
        'id': t.id, 'code': t.code, 'name': t.name,
        'description': t.description, 'avatar': t.avatar,
        'config': _safe_json(t.config),
    }


def _group_role(gid):
    m = GroupMember.query.filter_by(group_id=gid, user_id=g.user.id).first()
    return m.role if m else None


def _channel_role(cid):
    m = ChannelMember.query.filter_by(channel_id=cid, user_id=g.user.id).first()
    return m.role if m else None


def _users_by_ids(ids):
    if not ids:
        return []
    return User.query.filter(User.id.in_(list(ids))).all()


# ---------------------------------------------------------------- 模板列表
@bp.route('/templates', methods=['GET'])
@login_required
def list_templates():
    try:
        scope = request.args.get('scope', 'group')
        tpls = BotTemplate.query.filter_by(status=1).order_by(BotTemplate.id.asc()).all()
        group_only = {'welcome', 'leave_notice'}  # 入群/退群类仅群聊适用
        data = [_tpl_dict(t) for t in tpls if scope == 'group' or t.code not in group_only]
        return resp_json(OK, '获取成功', data={'templates': data})
    except Exception as e:
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


# ---------------------------------------------------------------- 群 bot
@bp.route('/group/<int:gid>/bots', methods=['GET'])
@login_required
def group_bots(gid):
    try:
        role = _group_role(gid)
        if role is None:
            return resp_json(PARAMS_ERROR, '你不在该群中', data=None)
        can_manage = role in (1, 2)
        tpls = BotTemplate.query.filter_by(status=1).order_by(BotTemplate.id.asc()).all()
        bindings = {b.bot_template_id: b for b in GroupBot.query.filter_by(group_id=gid).all()}
        bots = []
        for t in tpls:
            b = bindings.get(t.id)
            bots.append({
                **_tpl_dict(t),
                'default_config': _safe_json(t.config),
                'bound': b is not None,
                'enabled': b.enabled if b else 0,
                'config': _safe_json(b.config) if b else {},
            })
        return resp_json(OK, '获取成功', data={'can_manage': can_manage, 'my_role': role, 'bots': bots})
    except Exception as e:
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/group/<int:gid>/bind', methods=['POST'])
@login_required
def group_bind(gid):
    try:
        if _group_role(gid) not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        bot_template_id = (request.get_json(silent=True) or {}).get('bot_template_id')
        if not bot_template_id or not BotTemplate.query.get(bot_template_id):
            return resp_json(PARAMS_ERROR, 'Bot 模板不存在或未指定', data=None)
        exist = GroupBot.query.filter_by(group_id=gid, bot_template_id=bot_template_id).first()
        if exist:
            exist.enabled = 1
            db.session.commit()
            return resp_json(OK, '已启用', data={'bound': True})
        db.session.add(GroupBot(group_id=gid, bot_template_id=bot_template_id, enabled=1))
        db.session.commit()
        return resp_json(OK, '已添加', data={'bound': True})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/group/<int:gid>/unbind', methods=['POST'])
@login_required
def group_unbind(gid):
    try:
        if _group_role(gid) not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        bot_template_id = (request.get_json(silent=True) or {}).get('bot_template_id')
        b = GroupBot.query.filter_by(group_id=gid, bot_template_id=bot_template_id).first()
        if b:
            db.session.delete(b)
            db.session.commit()
        return resp_json(OK, '已移除', data={'bound': False})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/group/<int:gid>/toggle', methods=['POST'])
@login_required
def group_toggle(gid):
    try:
        if _group_role(gid) not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        data = request.get_json(silent=True) or {}
        bot_template_id = data.get('bot_template_id')
        enabled = 1 if int(data.get('enabled', 0)) == 1 else 0
        b = GroupBot.query.filter_by(group_id=gid, bot_template_id=bot_template_id).first()
        if not b:
            return resp_json(PARAMS_ERROR, '该 Bot 未绑定', data=None)
        b.enabled = enabled
        db.session.commit()
        return resp_json(OK, '已' + ('开启' if enabled else '关闭'), data={'enabled': enabled})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/group/<int:gid>/config', methods=['POST'])
@login_required
def group_config(gid):
    return _save_config(GroupBot, 'group_id', gid)


@bp.route('/group/<int:gid>/logs', methods=['GET'])
@login_required
def group_logs(gid):
    return _list_logs(BotLog.query.filter_by(group_id=gid), gid, 'group')


# ---------------------------------------------------------------- 频道 bot
@bp.route('/channel/<int:cid>/bots', methods=['GET'])
@login_required
def channel_bots(cid):
    try:
        role = _channel_role(cid)
        if role is None:
            return resp_json(PARAMS_ERROR, '你未加入该频道', data=None)
        can_manage = role in (1, 2)
        tpls = BotTemplate.query.filter_by(status=1).order_by(BotTemplate.id.asc()).all()
        bindings = {b.bot_template_id: b for b in ChannelBot.query.filter_by(channel_id=cid).all()}
        bots = []
        for t in tpls:
            if t.code in ('welcome', 'leave_notice'):
                continue  # 入群/退群类不适用于频道
            b = bindings.get(t.id)
            bots.append({
                **_tpl_dict(t),
                'default_config': _safe_json(t.config),
                'bound': b is not None,
                'enabled': b.enabled if b else 0,
                'config': _safe_json(b.config) if b else {},
            })
        return resp_json(OK, '获取成功', data={'can_manage': can_manage, 'my_role': role, 'bots': bots})
    except Exception as e:
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/channel/<int:cid>/bind', methods=['POST'])
@login_required
def channel_bind(cid):
    try:
        if _channel_role(cid) not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        bot_template_id = (request.get_json(silent=True) or {}).get('bot_template_id')
        if not bot_template_id or not BotTemplate.query.get(bot_template_id):
            return resp_json(PARAMS_ERROR, 'Bot 模板不存在或未指定', data=None)
        exist = ChannelBot.query.filter_by(channel_id=cid, bot_template_id=bot_template_id).first()
        if exist:
            exist.enabled = 1
            db.session.commit()
            return resp_json(OK, '已启用', data={'bound': True})
        db.session.add(ChannelBot(channel_id=cid, bot_template_id=bot_template_id, enabled=1))
        db.session.commit()
        return resp_json(OK, '已添加', data={'bound': True})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/channel/<int:cid>/unbind', methods=['POST'])
@login_required
def channel_unbind(cid):
    try:
        if _channel_role(cid) not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        bot_template_id = (request.get_json(silent=True) or {}).get('bot_template_id')
        b = ChannelBot.query.filter_by(channel_id=cid, bot_template_id=bot_template_id).first()
        if b:
            db.session.delete(b)
            db.session.commit()
        return resp_json(OK, '已移除', data={'bound': False})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/channel/<int:cid>/toggle', methods=['POST'])
@login_required
def channel_toggle(cid):
    try:
        if _channel_role(cid) not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        data = request.get_json(silent=True) or {}
        bot_template_id = data.get('bot_template_id')
        enabled = 1 if int(data.get('enabled', 0)) == 1 else 0
        b = ChannelBot.query.filter_by(channel_id=cid, bot_template_id=bot_template_id).first()
        if not b:
            return resp_json(PARAMS_ERROR, '该 Bot 未绑定', data=None)
        b.enabled = enabled
        db.session.commit()
        return resp_json(OK, '已' + ('开启' if enabled else '关闭'), data={'enabled': enabled})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


@bp.route('/channel/<int:cid>/config', methods=['POST'])
@login_required
def channel_config(cid):
    return _save_config(ChannelBot, 'channel_id', cid)


@bp.route('/channel/<int:cid>/logs', methods=['GET'])
@login_required
def channel_logs(cid):
    return _list_logs(BotLog.query.filter_by(channel_id=cid), cid, 'channel')


# ---------------------------------------------------------------- 共享实现
def _save_config(Model, col, target_id):
    try:
        role = _group_role(target_id) if col == 'group_id' else _channel_role(target_id)
        if role not in (1, 2):
            return resp_json(PARAMS_ERROR, '无权限操作', data=None)
        data = request.get_json(silent=True) or {}
        bot_template_id = data.get('bot_template_id')
        cfg = data.get('config', {})
        if not isinstance(cfg, (dict, list)):
            return resp_json(PARAMS_ERROR, 'config 需为对象', data=None)
        b = Model.query.filter_by(**{col: target_id, 'bot_template_id': bot_template_id}).first()
        if not b:
            return resp_json(PARAMS_ERROR, '该 Bot 未绑定', data=None)
        b.config = json.dumps(cfg, ensure_ascii=False)
        db.session.commit()
        return resp_json(OK, '配置已保存', data={'config': cfg})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)


def _list_logs(query, target_id, scope):
    try:
        page = max(1, int(request.args.get('page', 1)))
        size = min(50, max(1, int(request.args.get('size', 20))))
        total = query.count()
        rows = query.order_by(BotLog.created_at.desc()).offset((page - 1) * size).limit(size).all()
        tmap = {t.id: t.name for t in BotTemplate.query.all()}
        uids = {r.target_user_id for r in rows if r.target_user_id}
        umap = {u.id: u.nickname for u in _users_by_ids(uids)}
        logs = [{
            'id': r.id, 'bot': tmap.get(r.bot_template_id, 'Bot'),
            'action': r.action, 'reason': r.reason,
            'target_user_id': r.target_user_id,
            'target_name': umap.get(r.target_user_id) if r.target_user_id else None,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
        return resp_json(OK, '获取成功', data={'logs': logs, 'total': total, 'page': page})
    except Exception as e:
        return resp_json(SERVER_ERROR, f'服务器内部错误：{e}', data=None)
