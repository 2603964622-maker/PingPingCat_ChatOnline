"""计数类 bot 的时间窗口工具：基于项目 cache（Redis）的带 TTL 小计数/指纹。
只存整数计数、内容 hash、时间戳等极小数据，绝不存消息正文；窗口到期自动清零。"""
from exts import cache


def win_incr(key, window):
    """窗口内计数 +1 并返回当前值；key 到期自动归零。"""
    try:
        n = cache.get(key) or 0
    except Exception:
        n = 0
    n += 1
    try:
        cache.set(key, n, timeout=window)
    except Exception:
        pass
    return n


def win_get(key, default=None):
    try:
        v = cache.get(key)
        return default if v is None else v
    except Exception:
        return default


def win_set(key, value, window):
    try:
        cache.set(key, value, timeout=window)
    except Exception:
        pass


def win_delete(key):
    try:
        cache.delete(key)
    except Exception:
        pass
