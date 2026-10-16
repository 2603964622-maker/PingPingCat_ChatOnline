import click
import os
import re
from models.group  import GroupRoleEnum, GroupRole
from models.channel import ChannelRoleEnum, ChannelRole
from exts import db

# 缩略图副本自身的 key 后缀（{原图 key}.t400.jpg）：预热时要跳过，不能拿缩略图再去生缩略图
_THUMB_KEY_SUFFIX = re.compile(r'\.t\d+\.jpg$')


@click.option('--limit', default=0, show_default=True,
              help='最多扫多少个对象，0 表示不限制；先拿 50 试跑确认没问题再跑全量')
def warm_thumbs(limit):
    """给桶里存量图片一次性补齐缩略图，跑完用户就不会再当“第一个买单的人”。
    终端调用：flask warm-thumbs              # 全量扫描
              flask warm-thumbs --limit 50   # 先小批量试跑
    已生成的会自动跳过，所以可以反复重跑；上传时异步生成失败漏掉的图也会被这个命令兼顾。
    为什么直接扫对象存储而不是翻数据库：不必枚举每张表哪个字段存的是图，也不会漏掉没入库的对象。"""
    from flask import current_app
    from exts import get_s3_client, THUMB_SIZES
    from utils import ALLOWED_IMAGE_EXTENSIONS
    from blueprints.upload import build_thumbs_for_key

    client = get_s3_client()
    bucket = current_app.config['S3_BUCKET']
    scanned = handled = created = exists = failed = 0
    token = None
    click.echo(f"开始预热：桶 {bucket}，档位 {THUMB_SIZES}")
    while True:
        kwargs = {'Bucket': bucket, 'MaxKeys': 1000}
        if token:
            kwargs['ContinuationToken'] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get('Contents', []):
            key = obj['Key']
            scanned += 1
            if _THUMB_KEY_SUFFIX.search(key):
                continue
            ext = key.rsplit('.', 1)[-1].lower() if '.' in key else ''
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                continue
            stats = build_thumbs_for_key(key, THUMB_SIZES)
            handled += 1
            for st in stats.values():
                if st == 'created':
                    created += 1
                elif st == 'exists':
                    exists += 1
                elif st == 'error':
                    failed += 1
            if handled % 20 == 0:
                click.echo(f"进度：已扫 {scanned} 个对象 / 图片 {handled} 张，新建 {created}，已存在 {exists}，失败 {failed}")
            if limit and scanned >= limit:
                break
        if limit and scanned >= limit:
            break
        token = resp.get('NextContinuationToken')
        if not resp.get('IsTruncated') or not token:
            break
    click.echo(f"预热完成：扫描 {scanned} 个对象，处理图片 {handled} 张；"
               f"新建缩略图 {created} 张，已存在跳过 {exists} 张，失败 {failed} 张")


def init_business_role():
    """初始化群聊/频道业务角色到 group_roles / channel_roles 表（幂等，按 name 去重）。
    终端调用：flask init-business-role"""
    added, skipped = 0, 0
    for role in GroupRoleEnum:
        if GroupRole.query.filter_by(name=role.name).first():
            skipped += 1
            continue
        db.session.add(GroupRole(name=role.name, display_name=role.value))
        added += 1
    for role in ChannelRoleEnum:
        if ChannelRole.query.filter_by(name=role.name).first():
            skipped += 1
            continue
        db.session.add(ChannelRole(name=role.name, display_name=role.value))
        added += 1
    db.session.commit()
    click.echo(f"初始化业务角色权限完成：新增 {added}，已存在跳过 {skipped}")


def import_sensitive_words():
    """从项目根目录 sensitive_words.txt 导入敏感词库到 sensitive_words 表。
    终端调用：flask import-sensitive-words
    txt 每行：词,级别,分类,是否启用  （#,空行忽略；主键自增不写；word 已存在则跳过）
    """
    from models.sensitive_word import SensitiveWord
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sensitive_words.txt')
    if not os.path.exists(filepath):
        click.echo(f"未找到词库文件：{filepath}")
        return
    added, skipped, bad = 0, 0, 0
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',')]
            word = parts[0]
            if not word:
                bad += 1
                continue
            try:
                level = int(parts[1]) if len(parts) > 1 and parts[1] else 1
                category = parts[2] if len(parts) > 2 and parts[2] else 'default'
                is_active = int(parts[3]) if len(parts) > 3 and parts[3] else 1
            except ValueError:
                bad += 1
                continue
            if SensitiveWord.query.filter_by(word=word).first():
                skipped += 1
                continue
            db.session.add(SensitiveWord(word=word, level=level, category=category, is_active=is_active))
            added += 1
    db.session.commit()
    click.echo(f"敏感词导入完成：新增 {added}，已存在跳过 {skipped}，格式错误 {bad}（来源 {filepath}）")


# 内置 Bot 模板：code 为引擎派发的稳定标识，永不修改；config 为默认参数（各群可覆盖）
BOT_TEMPLATES = [
    ('antiflood', '防刷屏', '时间窗口内发送超上限 → 警告/禁言/踢',
     {'window': 10, 'limit': 6, 'action': 'mute', 'mute_minutes': 5}),
    ('antiad', '防广告链接', '新成员发链接 → 拦截+踢',
     {'only_new': True, 'new_minutes': 30, 'action': 'kick'}),
    ('antiimage', '防垃圾图片', '窗口内连续发图超量 → 拦截+禁言',
     {'window': 60, 'limit': 6, 'action': 'mute', 'mute_minutes': 10}),
    ('duplicate', '重复内容检测', '连续发相同内容 → 拦截+警告',
     {'window': 60, 'times': 3, 'action': 'warn'}),
    ('nightmute', '夜间禁言', '指定时段内非白名单成员发言 → 拦截',
     {'start': '23:00', 'end': '07:00', 'whitelist': []}),
    ('keyword_reply', '关键词自动回复', '命中关键词自动回复预设内容',
     {'rules': []}),
    ('welcome', '自动欢迎', '新成员入群自动发欢迎语（{nickname} 为占位符）',
     {'text': '欢迎 {nickname} 加入本群！'}),
    ('leave_notice', '退群通知', '有人退群通知管理员',
     {'text': '{nickname} 退出了群聊', 'notice_admins': True, 'broadcast': False}),
]


def init_bots():
    """初始化内置 Bot 模板到 bot_templates 表（幂等，按 code 去重）。
    终端调用：flask init-bots"""
    import json
    from models.bot import BotTemplate
    added, skipped = 0, 0
    for code, name, desc, cfg in BOT_TEMPLATES:
        if BotTemplate.query.filter_by(code=code).first():
            skipped += 1
            continue
        db.session.add(BotTemplate(
            code=code, name=name, description=desc,
            config=json.dumps(cfg, ensure_ascii=False), status=1))
        added += 1
    db.session.commit()
    click.echo(f"Bot 模板初始化完成：新增 {added}，已存在跳过 {skipped}")