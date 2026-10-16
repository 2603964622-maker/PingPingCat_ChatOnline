"""
内置 bot 规则处理函数。每个函数签名 handle(ctx, cfg) -> list[action]。

ctx 约定字段：
  scope('group'/'channel'), target_id, event, phase,
  sender_id, sender_name, text(消息内容), msg_type(1文本/2图片/3文件)
cfg：该 bot 在目标群/频道的生效配置（群级覆盖模板默认后合并而来）。

action 类型（由 engine 执行）：
  {'type':'block','msg'}      —— pre 阶段拦截本次发送
  {'type':'mute','minutes'}   —— 禁言
  {'type':'kick'}             —— 踢出（仅群）
  {'type':'warn','text'}      —— 群内公开警告（系统消息）
  {'type':'reply','text'}     —— 机器人发一条消息
  {'type':'notice_admins','text'} —— 通知群主/管理员
"""
import re
import hashlib
import datetime

from models.group import GroupMember
from models.channel import ChannelMember
from bots.window import win_incr, win_get, win_set, win_delete

# 链接/广告识别
_LINK_RE = re.compile(r'(https?://|www\.)|[\w-]+\.(com|cn|net|org|top|xyz|vip|shop|club)(/|\b|$)', re.I)


# ---------------------------------------------------------------- 内部工具
def _member_role(scope, target_id, uid):
    if scope == 'group':
        m = GroupMember.query.filter_by(group_id=target_id, user_id=uid).first()
    elif scope == 'channel':
        m = ChannelMember.query.filter_by(channel_id=target_id, user_id=uid).first()
    else:
        m = None
    return m.role if m else None


def _is_privileged(scope, target_id, uid):
    """群主/管理员（role 1/2）不受机器人审核处置。"""
    return _member_role(scope, target_id, uid) in (1, 2)


def _is_new_member(scope, target_id, uid, minutes):
    """入群是否在 minutes 分钟内（仅群聊有意义）。"""
    if scope != 'group':
        return False
    m = GroupMember.query.filter_by(group_id=target_id, user_id=uid).first()
    if not m or not m.joined_at:
        return True  # 查不到入群时间按新成员处理，偏保守
    delta = datetime.datetime.utcnow() - m.joined_at
    return delta <= datetime.timedelta(minutes=minutes)


def _in_time_range(now, start_hhmm, end_hhmm):
    """判断 now 是否落在 [start,end] 时段内，支持跨零点。"""
    try:
        sh, sm = [int(x) for x in start_hhmm.split(':')]
        eh, em = [int(x) for x in end_hhmm.split(':')]
    except Exception:
        return False
    cur = now.hour * 60 + now.minute
    s = sh * 60 + sm
    e = eh * 60 + em
    if s <= e:
        return s <= cur < e
    return cur >= s or cur < e  # 跨零点


# ---------------------------------------------------------------- 规则实现
def handle_antiflood(ctx, cfg):
    """防刷屏：窗口内发送超过上限 → 拦截 + 按配置处置。"""
    scope, target, sender = ctx['scope'], ctx['target_id'], ctx.get('sender_id')
    if _is_privileged(scope, target, sender):
        return []
    window = int(cfg.get('window', 10))
    limit = int(cfg.get('limit', 5))
    key = f"bot:flood:{scope}:{target}:{sender}"
    n = win_incr(key, window)
    if n <= limit:
        return []
    win_delete(key)
    acts = [{'type': 'block', 'msg': '发送过于频繁，请稍后再试'}]
    a = cfg.get('action', 'mute')
    if a == 'kick':
        acts.append({'type': 'kick'})
    elif a == 'warn':
        acts.append({'type': 'warn', 'target_user_id': sender,
                     'text': f"{ctx.get('sender_name', '有成员')} 请不要刷屏"})
    else:
        acts.append({'type': 'mute', 'minutes': int(cfg.get('mute_minutes', 5))})
    return acts


def handle_antiad(ctx, cfg):
    """防广告链接：（新）成员发链接 → 拦截 + 删/踢。"""
    scope, target, sender = ctx['scope'], ctx['target_id'], ctx.get('sender_id')
    if _is_privileged(scope, target, sender):
        return []
    if cfg.get('only_new', True) and not _is_new_member(scope, target, sender,
                                                         int(cfg.get('new_minutes', 30))):
        return []
    if not _LINK_RE.search(ctx.get('text') or ''):
        return []
    acts = [{'type': 'block', 'msg': '新成员暂不允许发送链接'}]
    a = cfg.get('action', 'kick')
    if a == 'mute':
        acts.append({'type': 'mute', 'minutes': int(cfg.get('mute_minutes', 10))})
    elif a == 'warn':
        acts.append({'type': 'warn', 'target_user_id': sender, 'text': '请勿发送广告链接'})
    else:
        acts.append({'type': 'kick'})
    return acts


def handle_antiimage(ctx, cfg):
    """防垃圾图片：窗口内连续发图超量 → 拦截 + 禁言。"""
    scope, target, sender = ctx['scope'], ctx['target_id'], ctx.get('sender_id')
    if ctx.get('msg_type', 1) != 2:  # 仅统计图片
        return []
    if _is_privileged(scope, target, sender):
        return []
    window = int(cfg.get('window', 60))
    limit = int(cfg.get('limit', 5))
    key = f"bot:img:{scope}:{target}:{sender}"
    n = win_incr(key, window)
    if n <= limit:
        return []
    win_delete(key)
    acts = [{'type': 'block', 'msg': '发送图片过于频繁，已拦截'}]
    a = cfg.get('action', 'mute')
    if a == 'kick':
        acts.append({'type': 'kick'})
    else:
        acts.append({'type': 'mute', 'minutes': int(cfg.get('mute_minutes', 10))})
    return acts


def handle_duplicate(ctx, cfg):
    """重复内容检测：连续发送相同内容达次数 → 拦截 + 警告/禁言。"""
    scope, target, sender = ctx['scope'], ctx['target_id'], ctx.get('sender_id')
    text = ctx.get('text') or ''
    if not text or _is_privileged(scope, target, sender):
        return []
    window = int(cfg.get('window', 60))
    times = int(cfg.get('times', 3))
    h = hashlib.md5(text.encode('utf-8')).hexdigest()
    key = f"bot:dup:{scope}:{target}:{sender}"
    state = win_get(key)
    cnt = state['c'] + 1 if (isinstance(state, dict) and state.get('h') == h) else 1
    win_set(key, {'h': h, 'c': cnt}, window)
    if cnt < times:
        return []
    win_delete(key)
    acts = [{'type': 'block', 'msg': '请勿重复发送相同内容'}]
    a = cfg.get('action', 'warn')
    if a == 'mute':
        acts.append({'type': 'mute', 'minutes': int(cfg.get('mute_minutes', 5))})
    else:
        acts.append({'type': 'warn', 'target_user_id': sender,
                     'text': f"{ctx.get('sender_name', '有成员')} 请勿重复刷屏"})
    return acts


def handle_nightmute(ctx, cfg):
    """夜间禁言：指定时段内非白名单成员发言 → 拦截删除。"""
    scope, target, sender = ctx['scope'], ctx['target_id'], ctx.get('sender_id')
    if _is_privileged(scope, target, sender):
        return []
    wl = set(cfg.get('whitelist', []) or [])
    if sender in wl:
        return []
    if _in_time_range(datetime.datetime.now(), str(cfg.get('start', '23:00')),
                      str(cfg.get('end', '07:00'))):
        return [{'type': 'block', 'msg': '当前处于夜间禁言时段，暂不可发言'}]
    return []


def handle_keyword_reply(ctx, cfg):
    """关键词自动回复：命中关键词回复预设内容（不拦截原消息）。"""
    text = ctx.get('text') or ''
    if not text:
        return []
    for r in (cfg.get('rules') or []):
        kw = r.get('keyword')
        reply = r.get('reply')
        if kw and reply and kw in text:
            return [{'type': 'reply',
                     'text': reply.replace('{nickname}', ctx.get('sender_name', ''))}]
    return []


def handle_welcome(ctx, cfg):
    """自动欢迎：新成员入群发欢迎语。"""
    name = ctx.get('sender_name') or ctx.get('nickname') or '新成员'
    text = str(cfg.get('text', '欢迎 {nickname} 加入本群！')).replace('{nickname}', name)
    return [{'type': 'reply', 'text': text}]


def handle_leave_notice(ctx, cfg):
    """退群通知：有人退群通知管理员（可选全群播报）。"""
    name = ctx.get('sender_name') or '某成员'
    text = str(cfg.get('text', '{nickname} 退出了群聊')).replace('{nickname}', name)
    acts = []
    if cfg.get('notice_admins', True):
        acts.append({'type': 'notice_admins', 'text': text})
    if cfg.get('broadcast', False):
        acts.append({'type': 'reply', 'text': text})
    return acts
