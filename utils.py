import os
import re
import uuid
import secrets
from datetime import datetime
from flask import current_app,jsonify,g
from models.user import User
from models.group import Group
from models.channel import Channel
from models.message import Conversation
import random
from exts import db
from exts import s3_upload_fileobj, s3_delete_object, s3_key_from_url


 # 1. 工具函数中不要用jsonify ，因为工具函数中不涉及响应，只涉及数据处理，return None即可
 # 2. 一个工具函数只有一个功能，不可设计多个功能，例如上传文件函数就只做"上传文件返回路由"这一件事情
 # 举例说明:
 #        工具函数中出错了"return None"也只是跳出了工具函数体，还是会继续执行路由函数的，假设我们给上传文件的工具函数设置
 #        检查文件大小，查出100MB就return& 上传 的两种功能，首先上传之前需要先检查文件大小，超出返回None,文件还没有上传
 #        那我们后端返回的json数据该怎么写呢?很显然，没有办法写;所以我们我们书写两个功能函数，分别是:
 #          函数1:检查文件大小是否超出文件限制,超出return None
 #          函数2:上传文件并返回路由
 #        这样的话我们后端的路由函数可以根据这个工具函数return None 来写不同的JSON信息了
#-------------------------------------------------------------------------------------------------------------
# 关于函数注释
# def function(形参):
#   """
#    用自然语言说明这个函数做什么、为什么存在（不是怎么实现）
#   :param type:描述函数所用形参类型
#   :return:返回值是什么,返回值异常/正确也要写
#   """
#--------------------------------------------------------------------------------------------------------------
def generate_unique_number(type):
    """
    根据 形参 type 生成带前缀的、不同种类的唯一编号 【用户、群聊、频道号】
    :param type: 字符串前缀 type,例如 "user_", "group_", "channel_"
    :return: type形参 + 随机生成的字符串
    """
    POOL = '0123456789'
    while True:
        # 使用加密安全随机源生成纯数字，避免可预测与高频碰撞
        digits = "".join(secrets.choice(POOL) for _ in range(10))
        full_number = type + digits
        # 按类型查对应表（判断查到的结果是否为空）
        if type == "user_":
            exists = User.query.filter_by(number=full_number).first()
        elif type == "group_":
            exists = Group.query.filter_by(number=full_number).first()
        elif type == "channel_":
            exists = Channel.query.filter_by(number=full_number).first()
        else:
            # 非法类型，直接返回 None 或抛异常
            return None
        # 核心：没查到才退出，查到了就继续循环重生成
        if not exists:
            break
    return full_number
#--------------------------------------------------------------------------------------------------------------
# 头像允许的文件扩展名
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv'}

def allowed_file_extend(filename,allowed_set):
    """ 检查头像文件扩展名是否合法 """
    return  filename.rsplit('.',1)[1].lower() in allowed_set
#--------------------------------------------------------------------------------------------------------------
def generate_filename(original_filename):
    """
    生成唯一文件名
    格式：时间戳_uuid_原始文件名.扩展名
    保留原始文件名主体，下载/反查时能认回真名（如 flask基础.pdf 不会变成纯时间戳名）；
    只过滤文件系统不安全字符，中文、空格、点号均保留
    """
    ext = original_filename.rsplit('.', 1)[1].lower()
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    # datetime.now():获取当前的时间【比如：2026年09月20日 19时56分15秒】
    # .strftime:时间对象的“格式化输出”方法
    unique_id = uuid.uuid4().hex[:8]
    # 默认的uuid是这样:7dec-11d0-a765-00a0c91e6bf6（带横杠，很长）
    # .hex:UUID对象属性，会把上面的标准UUID去掉横杠，只保留字符串
    # [:8]:Python字符串切片，只要这个字符串前八个字符
    stem = original_filename.rsplit('.', 1)[0]
    # 不用 secure_filename：它会把中文全部砍掉；只替换路径/Shell 危险字符
    safe_stem = re.sub(r'[\\/:*?"<>|\r\n]', '_', stem).strip()[:80] or 'file'
    return f"{timestamp}_{unique_id}_{safe_stem}.{ext}"
#--------------------------------------------------------------------------------------------------------------
def _uuid_object_name(original_filename):
    """对象存储中的存储名：纯 UUID + 扩展名（真实文件名另存 DB.filename）"""
    ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'bin'
    return f"{uuid.uuid4().hex}.{ext}"
#--------------------------------------------------------------------------------------------------------------
def _build_object_key(category, scope, original_filename):
    """
    拼对象存储 key：
      avatar + scope=('user',8)      -> avatar/8/{uuid}.ext
      private + scope=('private',30) -> private/30/{uuid}.ext  (混放)
      其它   + scope=('channel',12)  -> photo/channel/12/{uuid}.ext
      无 scope                        -> {category}/{uuid}.ext
    """
    name = _uuid_object_name(original_filename)
    if not scope:
        return f"{category}/{name}"
    domain, obj_id = scope
    if category == 'avatar':
        return f"avatar/{obj_id}/{name}"
    if domain == 'private':
        return f"private/{obj_id}/{name}"
    return f"{category}/{domain}/{obj_id}/{name}"
#--------------------------------------------------------------------------------------------------------------
def _log_file(action, url, file_type=None, scene=None, size=None):
    """向 FileLog 写一条文件上传/删除流水（供管理端统计上传/删除比率与上传总量）。
    仅 add 不 commit，交由外层业务事务一并提交；尽力从 g.user 取操作者 id。
    任何异常（无 app/db 上下文等）静默吞掉，绝不影响主流程。"""
    try:
        from models.report import FileLog
        uid = None
        try:
            cu = getattr(g, 'user', None)
            uid = cu.id if cu is not None else None
        except Exception:
            uid = None
        db.session.add(FileLog(action=action, file_url=(url or '')[:500] or None,
                               file_type=file_type, user_id=uid, scene=scene,
                               file_size=int(size) if size and size > 0 else None))
    except Exception:
        pass
#--------------------------------------------------------------------------------------------------------------
def _peek_file_size(file):
    """不消耗文件地读取字节大小（seek 到末尾取位置后回到开头），失败返回 None。"""
    try:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        return size
    except Exception:
        return None
#--------------------------------------------------------------------------------------------------------------
def _build_thumb_sync(url, sizes=(400,)):
    """上传后同步把主力档位（列表用的 400）生成好。
    为什么敢多花这一两秒：用户此刻正盯着上传进度条，是整条链路里最不在意延迟的一刻；
    而“发布必有缩略图”这件事只要还依赖 worker，本地开发与 worker 挂掉时就会悄悄退化。
    任何失败都不能让上传本身失败，最坏退回按需生成。"""
    try:
        from blueprints.upload import build_thumbs_for_url   # 延迟导入：upload 蓝图导入了本模块，模块级导入会循环
        build_thumbs_for_url(url, sizes)
    except Exception as e:
        current_app.logger.info(f"同步生成缩略图失败，将回退为按需生成: {e}")
#--------------------------------------------------------------------------------------------------------------
def _queue_build_thumbs(url):
    """图片上传成功后把“生成缩略图”丢给 Celery，上传接口本身不等它。
    worker 没起 / Redis 连不上都不能让上传失败：那种情况下最坏只是退回“按需生成”，
    所以整体 try/except，并只记 info 日志。"""
    try:
        current_app.celery.send_task('build_thumbs', args=[url])
    except Exception as e:
        current_app.logger.info(f"缩略图异步任务投递失败，将回退为按需生成: {e}")
#--------------------------------------------------------------------------------------------------------------
def save_upload_file(file, force_category=None, scope=None):
    """
    统一上传入口：upload_fileobj 上传到对象存储，返回公网直链 URL。
    对象名用 UUID，真实文件名通过第三个返回值交给业务表 filename 字段保存。
    图片 photo(3) / 视频 video(2) / 其他 other(4) / 头像 avatar(1)

    :param file: request.files['file'] 传进来的文件对象
    :param force_category: 强制指定类型，'avatar' 时走头像逻辑（仅图片）
    :param scope: (domain, obj_id) 元组，决定 key 目录，如 ('channel',12)/('group',5)/('private',30)/('user',8)/('moment',8)
    :return: 成功返回 (公网URL, file_type, 原始文件名) 三元组；失败返回 None
    """
    if not file or not file.filename:
        return None

    # 判定类型 / 目录
    if force_category == 'avatar':
        if not allowed_file_extend(file.filename, ALLOWED_IMAGE_EXTENSIONS):
            return None
        category, file_type = 'avatar', 1
    else:
        ext = file.filename.rsplit('.', 1)[1].lower()
        if ext in ALLOWED_IMAGE_EXTENSIONS:
            category, file_type = 'photo', 3
        elif ext in ALLOWED_VIDEO_EXTENSIONS:
            category, file_type = 'video', 2
        else:
            category, file_type = 'other', 4

    key = _build_object_key(category, scope, file.filename)
    size = _peek_file_size(file)
    try:
        url = s3_upload_fileobj(file, key)
    except Exception as e:
        current_app.logger.exception(f"S3 上传失败: {e}")
        return None
    _log_file('upload', url, file_type, scope[0] if scope else category, size=size)
    if file_type in (1, 3) and current_app.config.get('THUMB_ENABLED', True):
        # 1=头像 3=图片：只有这两类需要派生缩略图，视频与文档跳过
        # 体积闸门：同步生成要把原图再从桶拉回一次，链路慢时会把上传接口拖死，
        # 所以只对小体积图硬保证“发布必有图”，大文件交给 Celery（生产 worker 在跑）与按需生成。
        budget = current_app.config.get('THUMB_SYNC_MAX_BYTES') or 0
        if size and size <= budget:
            _build_thumb_sync(url)
        _queue_build_thumbs(url)   # 补齐其余档位；400 已做过会被 exists 跳过，代价只有两次 HEAD
    return url, file_type, file.filename
#--------------------------------------------------------------------------------------------------------------
def delete_old_file(stored):
    """
    删除旧文件，自动识别介质：
      对象存储 URL / key -> delete_object
      本地相对路径        -> 删磁盘
    :param stored: 数据库里存的路径或 URL
    """
    if not stored:
        return
    _log_file('delete', stored)
    if stored.startswith('http'):
        # 仅删除本桶对象；外链（如 robohash 默认头像）跳过
        if s3_key_from_url(stored):
            try:
                s3_delete_object(stored)
            except Exception as e:
                current_app.logger.exception(f"S3 删除失败: {e}")
        return
    # 兼容尚未迁移的历史记录：本地 /static/ 相对路径，删磁盘文件
    path = stored.lstrip('/')
    abs_path = os.path.join(current_app.root_path, path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
#--------------------------------------------------------------------------------------------------------------
def get_file_size(file):
    """
    获取上传文件的字节大小
    :param file: request.files.get('file')拿到的 FileStorage对象
    :return: 文件大小（字节），超出限制或失败返回 None
    """
    # 设置文件单次上传限制
    MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100MB
    file.seek(0, os.SEEK_END)  #seek(位置,参考点): 移动文件读写指针  os.SEEK_END 表示文件末尾
    size = file.tell()     # 返回当前指针所在的位置(字节数)
    file.seek(0)            # 回到文件开头
    if size > MAX_FILE_SIZE_BYTES:
        return None
    return size
#--------------------------------------------------------------------------------------------------------------
def get_avatar_size(file):
    """获取头像/背景图字节大小，超过 10MB 返回 None"""
    MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE_BYTES:
        return None
    return size
#--------------------------------------------------------------------------------------------------------------
PARAMS_ERROR = 400
SERVER_ERROR = 500
OK = 0
def resp_json(code=OK,msg="",data=None):
    return jsonify(code=code,msg=msg,data=data)
#---------------------------------------------------------------------------------------------------------------
def quote_preview(msg_type, content, filename=None, deleted=False):
    """
    为“引用回复”生成被引用消息的可读摘要：文本取前 60 字，媒体/文件/相册/转发用占位标签，已撤回显示[已撤回]。
    :return: 用于气泡引用块展示的短字符串
    """
    if deleted:
        return '[已撤回]'
    mt = msg_type or 1
    if mt == 1:
        return (content or '')[:60]
    if mt == 2:
        return '[图片]'
    if mt == 3:
        return ('[文件] ' + (filename or '')).strip()
    if mt == 4:
        return '[相册]'
    if mt == 5:
        return '[转发]'
    return (content or '')[:60]
# ===== 敏感词过滤：本地词库（DB）+ 内存缓存 + 命中即拦截 + 违规计数 + 阈值禁言/踢出 =====
# 处置阈值（可调）：累计命中达 MUTE_THRESHOLD 次禁言 MUTE_MINUTES 分钟；群内达 KICK_THRESHOLD 次踢出
SENSITIVE_MUTE_THRESHOLD = 5
SENSITIVE_MUTE_MINUTES = 10
SENSITIVE_KICK_THRESHOLD = 10

_SENSITIVE_CACHE = {'words': None}  # list[(word, level)]，首次使用时从库加载，词库变更后置 None 失效


def invalidate_sensitive_cache():
    """词库增删改后调用，令下次匹配重新加载"""
    _SENSITIVE_CACHE['words'] = None


def _load_sensitive_words():
    if _SENSITIVE_CACHE['words'] is None:
        from models.sensitive_word import SensitiveWord
        _SENSITIVE_CACHE['words'] = [
            (w.word, w.level or 1) for w in SensitiveWord.query.filter_by(is_active=1).all() if w.word
        ]
    return _SENSITIVE_CACHE['words']


def check_sensitive(text):
    """对文本做敏感词匹配（子串命中）。返回 (命中词列表, 最高级别)；未命中返回 ([], 0)。"""
    if not text:
        return [], 0
    hit, max_level = [], 0
    for w, lv in _load_sensitive_words():
        if w in text:
            hit.append(w)
            if lv > max_level:
                max_level = lv
    return hit, max_level


def refresh_user_ban(user):
    """定时封禁到期自动解封：status=0 且 banned_until 已过 → 恢复 status=1 并清空 banned_until。
    永久封禁（banned_until 为空）不受影响。返回是否发生了变更。
    （MySQL DATETIME 读回为 naive UTC，比较前补 tzinfo）"""
    import datetime as _dt
    if user is None or user.status != 0 or user.banned_until is None:
        return False
    bu = user.banned_until
    if bu.tzinfo is None:
        bu = bu.replace(tzinfo=_dt.timezone.utc)
    if bu <= _dt.datetime.now(_dt.timezone.utc):
        user.status = 1
        user.banned_until = None
        db.session.commit()
        return True
    return False


def is_user_muted(user):
    """全局禁言判定（MySQL DATETIME 读回为 naive UTC，需补 tzinfo 再比较）"""
    mu = getattr(user, 'muted_until', None)
    if not mu:
        return False
    import datetime as _dt
    if mu.tzinfo is None:
        mu = mu.replace(tzinfo=_dt.timezone.utc)
    return mu > _dt.datetime.now(_dt.timezone.utc)


def _record_and_punish(user_id, scene, group_id, hit_words, max_level):
    """写一条违规流水，按用户累计违规数触发禁言/踢出。返回 None/'mute'/'kick'。"""
    import datetime as _dt
    from models.sensitive_word import SensitiveViolation
    from models.user import User
    from models.group import GroupMember
    now = _dt.datetime.now(_dt.timezone.utc)
    db.session.add(SensitiveViolation(
        user_id=user_id, scene=scene, words=','.join(hit_words)[:255],
        level=max_level, group_id=group_id, created_at=now))
    db.session.commit()  # 违规记录独立提交（消息本身不落库）
    total = SensitiveViolation.query.filter_by(user_id=user_id).count()
    # 群内达踢出阈值：移出成员（群主除外）
    if scene == 'group' and group_id and total >= SENSITIVE_KICK_THRESHOLD:
        m = GroupMember.query.filter_by(user_id=user_id, group_id=group_id).first()
        if m and m.role != 1:
            db.session.delete(m)
            create_notification(user_id, 'sensitive_kick', title='你已被移出群聊',
                                content='因多次发送违规内容，你已被自动移出该群',
                                related_id=group_id, related_type='group')
            db.session.commit()
            return 'kick'
        return None
    # 达禁言阈值：全局禁言（群内同时设成员禁言至同一时刻）
    if total >= SENSITIVE_MUTE_THRESHOLD:
        until = now + _dt.timedelta(minutes=SENSITIVE_MUTE_MINUTES)
        user = User.query.get(user_id)
        if user:
            user.muted_until = until
        if scene == 'group' and group_id:
            m = GroupMember.query.filter_by(user_id=user_id, group_id=group_id).first()
            if m:
                m.mute_until = until
        create_notification(user_id, 'sensitive_mute', title='你已被禁言',
                            content=f'因多次发送违规内容，已被禁言 {SENSITIVE_MUTE_MINUTES} 分钟',
                            related_id=group_id, related_type=scene)
        db.session.commit()
        return 'mute'
    return None


def guard_sensitive(text, user_id, scene='private', group_id=None):
    """发送前守卫：命中敏感词则拦截（不存库）+ 记录违规 + 阈值处置。
    返回 (ok, msg)：ok=True 表示无敏感词可继续发送；ok=False 时 msg 为给用户的提示。"""
    hit, max_level = check_sensitive(text)
    if not hit:
        return True, ''
    punish = _record_and_punish(user_id, scene, group_id, hit, max_level)
    if punish == 'kick':
        return False, '多次严重违规，你已被自动移出该群'
    if punish == 'mute':
        return False, f'多次发送违规内容，消息已拦截，你已被禁言 {SENSITIVE_MUTE_MINUTES} 分钟'
    return False, '消息包含违规内容，已拦截发送'
#---------------------------------------------------------------------------------------------------------------
def create_notification(user_id, type, title=None, content=None, related_id=None, related_type=None):
    """
    创建一条站内通知（给接收者 user_id）
    自动避免给自己发通知（user_id 为空时直接跳过）
    :return: Notification 对象或 None
    """
    from models.notification import Notification
    if not user_id:
        return None
    note = Notification(
        user_id=user_id, type=type, title=title, content=content,
        related_id=related_id, related_type=related_type
    )
    db.session.add(note)
    return note
#---------------------------------------------------------------------------------------------------------------
def upsert_conversation(user_id, conv_type, target_id, last_msg_id=None, last_content=None, increment_unread=False):
    """
    新增/刷新一个会话条目（收到消息时调用，按 updated_at 置顶）
    :param increment_unread: 接收方传 True（未读+1），发送方传 False（不增加自己未读）
    """
    from models.message import UserConversation
    conv = UserConversation.query.filter_by(user_id=user_id, conv_type=conv_type, target_id=target_id).first()
    if conv:
        conv.last_msg_id = last_msg_id
        conv.last_content = (last_content or '')[:255] or None
        if increment_unread:
            conv.unread_count = (conv.unread_count or 0) + 1
        # 修改字段会触发 onupdate 自动刷新 updated_at
    else:
        conv = UserConversation(
            user_id=user_id, conv_type=conv_type, target_id=target_id,
            last_msg_id=last_msg_id, last_content=(last_content or '')[:255] or None,
            unread_count=(1 if increment_unread else 0)
        )
        db.session.add(conv)
    return conv
#---------------------------------------------------------------------------------------------------------------
def touch_private_conversation(from_user_id, to_user_id, msg_id, content):
    """私聊：为双方各维护一条会话（target 为对方），发送方不增未读，接收方未读+1"""
    upsert_conversation(from_user_id, 'private', to_user_id, msg_id, content, increment_unread=False)
    upsert_conversation(to_user_id, 'private', from_user_id, msg_id, content, increment_unread=True)
#---------------------------------------------------------------------------------------------------------------
def touch_group_conversation(group_id, msg_id, content, sender_id):
    """群聊：为所有群成员刷新会话，除发送者外未读+1"""
    from models.group import GroupMember
    for m in GroupMember.query.filter_by(group_id=group_id).all():
        upsert_conversation(m.user_id, 'group', group_id, msg_id, content,
                            increment_unread=(m.user_id != sender_id))
#---------------------------------------------------------------------------------------------------------------
def touch_channel_conversation(channel_id, post_id, content, sender_id):
    """频道：为所有频道成员刷新会话，除发布者外未读+1"""
    from models.channel import ChannelMember
    for m in ChannelMember.query.filter_by(channel_id=channel_id).all():
        upsert_conversation(m.user_id, 'channel', channel_id, post_id, content,
                            increment_unread=(m.user_id != sender_id))
#---------------------------------------------------------------------------------------------------------------