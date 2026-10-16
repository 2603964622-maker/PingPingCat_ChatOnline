"""在线状态（presence）：单进程内存注册表 + 笼统的“最近活跃”分档文案。

- 在线：用户当前有活跃的 SocketIO 连接（同一账号多标签页用连接计数）。
- 离线：按 users.last_online 距今时长分档，给出笼统描述，不暴露精确时间。
说明：SocketIO 以单进程 threading 模式运行，进程内计数即可满足“笼统”判定；
服务重启后在线态自然清零，用户重连即恢复，last_online 仍持久化在数据库。
"""
from datetime import datetime, timezone

# uid -> 活跃连接数（同一用户多端/多标签页）
_conn = {}


def mark_online(uid):
    """连接数 +1；返回 True 表示该用户刚由离线转在线（0→1），用于开启在线会话流水。"""
    n = _conn.get(uid, 0) + 1
    _conn[uid] = n
    return n == 1


def mark_offline(uid):
    """连接数 -1；返回 True 表示该用户已完全离线（归零）。"""
    n = _conn.get(uid, 0) - 1
    if n > 0:
        _conn[uid] = n
        return False
    _conn.pop(uid, None)
    return True


def is_online(uid):
    return _conn.get(uid, 0) > 0


def online_users():
    """当前在线的 {uid: 连接数} 快照（拷一份，避免调用方改到注册表本身）。
    这是“谁在线”的唯一权威数据源：不依赖数据库流水是否写入成功。
    注意：仍是单进程内存态，将来多副本/多 worker 部署时每个进程只看得见自己
    那部分连接，得换成 Redis 集合（SADD + 心跳 TTL）才能汇总。"""
    return dict(_conn)


def _as_utc(dt):
    """MySQL DATETIME 读回为无时区的 naive UTC，统一补上 tzinfo 以便比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# 分档阈值（秒）
_JUST_NOW = 5 * 60          # 刚刚曾上线
_RECENT = 2 * 24 * 3600     # 最近（两天内）曾上线
_WEEK = 7 * 24 * 3600       # 一周内曾上线
_MONTH = 30 * 24 * 3600     # 一个月内曾上线


def describe(user):
    """返回 {online, last_online, presence_text}，供资料卡 / 私聊头部等复用。"""
    online = is_online(user.id)
    lo = _as_utc(user.last_online)
    if online:
        text = '在线'
    elif lo is None:
        text = '很久没上线'
    else:
        diff = (datetime.now(timezone.utc) - lo).total_seconds()
        if diff < 0:
            diff = 0
        if diff <= _JUST_NOW:
            text = '刚刚曾上线'
        elif diff <= _RECENT:
            text = '最近曾上线'
        elif diff <= _WEEK:
            text = '一周内曾上线'
        elif diff <= _MONTH:
            text = '一个月内曾上线'
        else:
            text = '很久没上线'
    return {
        'online': online,
        'last_online': user.last_online.isoformat() if user.last_online else None,
        'presence_text': text,
    }
