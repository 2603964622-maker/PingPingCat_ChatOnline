"""
Bot 引擎：事件驱动的规则派发器。

设计要点：
- Bot 不是"配置时调用"，而是"运行时事件触发"。配置只写绑定关系与参数（group_bots/channel_bots）。
- 每当群/频道产生事件（发消息/入群/退群），对应处理函数调用 run_bots()，
  引擎查出该对象启用的 bot，逐个把事件丢给规则处理函数，收集动作并执行 + 记 BotLog。
- 事件分两个阶段：
    pre  —— 消息落库/广播【之前】，可返回 block 拦截本次发送（防刷屏/防广告/防垃圾图/重复内容/夜间禁言）。
    post —— 事件发生【之后】，只产生副作用（自动欢迎/退群通知/关键词回复）。
"""
import json
import datetime

from exts import db, cache, socketio
from models.bot import BotTemplate, GroupBot, ChannelBot, BotLog
from models.group import GroupMember
from models.user import User
from utils import create_notification

from bots import rules
from bots.window import win_incr, win_get, win_set, win_delete  # 供规则使用，同时在此重导出

# code -> 处理函数
HANDLERS = {
    'antiflood': rules.handle_antiflood,
    'antiad': rules.handle_antiad,
    'antiimage': rules.handle_antiimage,
    'duplicate': rules.handle_duplicate,
    'nightmute': rules.handle_nightmute,
    'keyword_reply': rules.handle_keyword_reply,
    'welcome': rules.handle_welcome,
    'leave_notice': rules.handle_leave_notice,
}

# code -> (适用事件, 阶段)
BOT_META = {
    'antiflood': ('message', 'pre'),
    'antiad': ('message', 'pre'),
    'antiimage': ('message', 'pre'),
    'duplicate': ('message', 'pre'),
    'nightmute': ('message', 'pre'),
    'keyword_reply': ('message', 'post'),
    'welcome': ('member_join', 'post'),
    'leave_notice': ('member_leave', 'post'),
}


# ------------------------------------------------------------------ Redis 窗口
# 计数类 bot 的窗口工具集中在 bots.window（基于 cache/Redis 的带 TTL 计数），此处仅重导出。


# ------------------------------------------------------------------ 绑定与配置
def _bindings(scope, target_id):
    """返回该群/频道启用的 [(BotTemplate, cfg_dict)]，cfg 为群级覆盖模板默认后的生效配置。"""
    if scope == 'group':
        rows = GroupBot.query.filter_by(group_id=target_id, enabled=1).all()
    elif scope == 'channel':
        rows = ChannelBot.query.filter_by(channel_id=target_id, enabled=1).all()
    else:
        rows = []
    out = []
    for rb in rows:
        tpl = BotTemplate.query.get(rb.bot_template_id)
        if not tpl or tpl.status != 1 or not tpl.code:
            continue
        out.append((tpl, _effective_cfg(tpl, rb)))
    return out


def _effective_cfg(tpl, binding):
    cfg = {}
    try:
        if tpl.config:
            cfg.update(json.loads(tpl.config))
    except Exception:
        pass
    try:
        bc = getattr(binding, 'config', None)
        if bc:
            cfg.update(json.loads(bc))
    except Exception:
        pass
    return cfg


# ------------------------------------------------------------------ 主入口
def run_bots(scope, target_id, event, phase, ctx):
    """
    执行某事件+阶段下所有启用的 bot。
    :return: (blocked, msg) —— blocked=True 表示 pre 阶段被拦截，msg 为给用户的提示。
    """
    ctx.setdefault('scope', scope)
    ctx.setdefault('target_id', target_id)
    ctx.setdefault('event', event)
    blocked, block_msg = False, ''
    for tpl, cfg in _bindings(scope, target_id):
        meta = BOT_META.get(tpl.code)
        if not meta or meta != (event, phase):
            continue
        handler = HANDLERS.get(tpl.code)
        if not handler:
            continue
        try:
            acts = handler(ctx, cfg) or []
        except Exception as e:
            print(f"[bot {tpl.code}] 处理异常：{e}")
            continue
        for act in acts:
            if act.get('type') == 'block':
                if not blocked:
                    blocked, block_msg = True, act.get('msg') or '消息已被机器人拦截'
            else:
                _apply_action(scope, target_id, tpl, act, ctx)
    return blocked, block_msg


# ------------------------------------------------------------------ 动作执行
def _apply_action(scope, target_id, tpl, act, ctx):
    atype = act.get('type')
    sender_id = ctx.get('sender_id')
    if atype == 'mute':
        _do_mute(scope, target_id, act.get('target_user_id') or sender_id,
                 int(act.get('minutes', 10) or 0), tpl, ctx)
    elif atype == 'kick':
        _do_kick(scope, target_id, act.get('target_user_id') or sender_id, tpl, ctx)
    elif atype == 'warn':
        _emit_bot_message(scope, target_id, act.get('text') or '请注意群内秩序', tpl.name)
        _log(tpl, scope, target_id, act.get('target_user_id') or sender_id, 'warning', act.get('text'))
    elif atype == 'reply':
        _emit_bot_message(scope, target_id, act.get('text') or '', tpl.name)
    elif atype == 'notice_admins':
        _do_notice_admins(scope, target_id, act.get('text') or '', tpl)


def _do_mute(scope, target_id, user_id, minutes, tpl, ctx):
    if not user_id or minutes <= 0:
        return
    until = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
    if scope == 'group':
        m = GroupMember.query.filter_by(group_id=target_id, user_id=user_id).first()
        if m and m.role != 1:  # 群主免疫
            m.mute_until = until
            db.session.commit()
            _notify(user_id, 'group_mute', '你已被禁言',
                    f'你在该群已被机器人禁言 {minutes} 分钟', target_id, 'group')
            _log(tpl, scope, target_id, user_id, 'mute', f'禁言 {minutes} 分钟')
    else:  # 频道无成员禁言字段，走全局禁言
        u = User.query.get(user_id)
        if u:
            u.muted_until = until
            db.session.commit()
            _notify(user_id, 'channel_mute', '你已被禁言',
                    f'你已被机器人全局禁言 {minutes} 分钟', target_id, 'channel')
            _log(tpl, scope, target_id, user_id, 'mute', f'禁言 {minutes} 分钟')


def _do_kick(scope, target_id, user_id, tpl, ctx):
    if scope != 'group' or not user_id:
        return
    m = GroupMember.query.filter_by(group_id=target_id, user_id=user_id).first()
    if m and m.role != 1:  # 群主不可被踢
        db.session.delete(m)
        db.session.commit()
        _notify(user_id, 'group_kick', '你已被移出群聊', '机器人将你移出群聊', target_id, 'group')
        _log(tpl, scope, target_id, user_id, 'kick', '机器人踢出')


def _do_notice_admins(scope, target_id, text, tpl):
    if scope != 'group':
        return
    managers = GroupMember.query.filter(
        GroupMember.group_id == target_id, GroupMember.role.in_([1, 2])).all()
    for gm in managers:
        _notify(gm.user_id, 'group_notice', '群动态', text, target_id, 'group')
    _log(tpl, scope, target_id, None, 'notice', text)


def _notify(user_id, ntype, title, content, related_id, related_type):
    create_notification(user_id, ntype, title=title, content=content,
                        related_id=related_id, related_type=related_type)
    try:
        socketio.emit('notification', {
            'type': ntype, 'title': title, 'content': content,
            'related_id': related_id, 'related_type': related_type
        }, room=str(user_id))
    except Exception:
        pass


def _emit_bot_message(scope, target_id, text, bot_name):
    """向房间广播一条机器人系统消息（不入库，仅实时展示）。"""
    if not text:
        return
    if scope == 'group':
        room = f"group_{target_id}"
        socketio.emit('bot_message', {
            'group_id': target_id, 'content': text,
            'bot_name': bot_name or '机器人',
            'created_at': datetime.datetime.utcnow().isoformat()
        }, room=room)
    elif scope == 'channel':
        room = f"channel_{target_id}"
        socketio.emit('bot_message', {
            'channel_id': target_id, 'content': text,
            'bot_name': bot_name or '机器人',
            'created_at': datetime.datetime.utcnow().isoformat()
        }, room=room)


def _log(tpl, scope, target_id, target_user_id, action, reason):
    try:
        db.session.add(BotLog(
            bot_template_id=tpl.id,
            group_id=target_id if scope == 'group' else None,
            channel_id=target_id if scope == 'channel' else None,
            target_user_id=target_user_id,
            action=action,
            reason=(reason or '')[:200] or None,
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[bot log] 失败：{e}")
