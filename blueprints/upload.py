from flask import Blueprint,render_template,request,jsonify,session,g,redirect,current_app
import io
import time


from exts import db, cache
from exts import s3_presigned_download_url
from exts import (s3_key_from_url, s3_public_url, s3_object_exists, s3_get_bytes,
                  s3_put_bytes, s3_thumb_key, THUMB_SIZES)
from models.upload import AvatarFile
from utils import save_upload_file,delete_old_file,get_file_size,generate_filename,get_avatar_size,resp_json
from decorators import login_required
from utils import ALLOWED_IMAGE_EXTENSIONS,ALLOWED_VIDEO_EXTENSIONS
bp = Blueprint('upload',__name__,url_prefix='/upload')

#TODO : 生产环境中 记得把上传文件改为对象存储，开发环境先存储到项目文件夹中的/static中

# 缩略图：原图字节永不改动，只按需派生一份小副本（{key}.t{边长}.jpg）。
# 列表/网格一律下载缩略图，点开大图/另存为仍走原图，因此画质零损失。
THUMB_NO_PROCESS_EXT = {'gif'}           # 动图取首帧会丢动画，直接回原图
THUMB_MAX_SRC_BYTES = 40 * 1024 * 1024   # 超 40MB 的原图不在服务端解码，宁可让前端多下载，也不能打挂 worker
THUMB_LOCK_TTL = 120                     # 单张缩略图的生成锁存活秒数：远大于一次生成耗时，又能在 worker 进程崩溃后靠过期自动释放


def _thumb_enabled():
    """缩略图总开关（config.THUMB_ENABLED）：关掉后请求直接回原图，上传也不再派生副本。
    开发机到桶的上行很慢，现生成比原图还慢得多，调试时没理由非走这条路不可。"""
    return bool(current_app.config.get('THUMB_ENABLED', True))


def _thumb_lock(thumb_key):
    """尝试占住这张缩略图的生成权（Redis SETNX 语义）。
    没锁的话，同一张图被 N 个人同时首次访问就会生成 N 遍，N 次下载 + N 次上传互相抢带宽。
    返回 True=可以生成；False=已有人在生成。
    两种“拿不到锁”必须区分开，否则锁会把缩略图功能整体关掉：
      1. 真的有人持有锁   -> 本次回原图；
      2. 缓存后端不可用 -> cachelib 连不上 Redis 时不抛异常，只返回 None，看起来也“没拿到”，
         所以再用 get 确认一次：锁 key 根本不存在 / 或值已老到超过两个 TTL（setnx 与 expire
         中间崩溃留下的死锁），都当作没人占用，照常生成。宁可多算也不能不出图。"""
    lk = 'thumb_lock:' + thumb_key
    now = time.time()
    try:
        if cache.add(lk, now, timeout=THUMB_LOCK_TTL):
            return True
        held = cache.get(lk)
        return held is None or now - float(held) > THUMB_LOCK_TTL * 2
    except Exception:
        return True


def _thumb_unlock(thumb_key):
    """释放生成权：生成失败时下一个请求才能立刻重试，而不是干等锁自然过期"""
    try:
        cache.delete('thumb_lock:' + thumb_key)
    except Exception:
        pass


def _decode_source(key):
    """下载并解码原图（按 EXIF 转正），不做缩放。
    单独拆出来是为了“多档一次生成时原图只下载解码一次”——否则 400/900 两个档位
    就要各拉一遍原图，预热与上传后异步生成都会被这份重复流量拖慢一倍。"""
    from PIL import Image, ImageOps   # 延迟导入：没装 Pillow 时只降级为“无缩略图”，不影响启动
    Image.MAX_IMAGE_PIXELS = 300_000_000   # 默认 1.78 亿像素上限会误杀高像素手机照与长截图
    raw = s3_get_bytes(key)
    if not raw or len(raw) > THUMB_MAX_SRC_BYTES:
        raise ValueError('原图为空或过大，不做缩略')
    return ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))


def _encode_thumb(src, size):
    """从解码结果缩出指定档位的 JPEG 字节。全程只读源图，原图本身永不改动。"""
    from PIL import Image
    im = src.copy()                       # 复制一份再缩，避免就地改动影响下一个档位
    im.thumbnail((size, size), Image.LANCZOS)   # thumbnail 只缩不放，小图不会被撑大变糊
    if im.mode != 'RGB':
        if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
            rgba = im.convert('RGBA')
            base = Image.new('RGB', rgba.size, (255, 255, 255))
            base.paste(rgba, mask=rgba.split()[-1])   # 透明底垫白，否则转 JPEG 会变黑块
            im = base
        else:
            im = im.convert('RGB')
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=80, optimize=True, progressive=True)
    return buf.getvalue()


def _make_thumb(key, thumb_key, size):
    """单档生成：请求路径上一次只缺一个档位，直接缩完回写"""
    s3_put_bytes(_encode_thumb(_decode_source(key), size), thumb_key, 'image/jpeg')


def build_thumbs_for_key(key, sizes=None):
    """补齐一张已上传图片的各档缩略图，已存在的跳过、动图/非图跳过。
    供两处复用：上传后投递的 Celery 异步任务、以及 flask warm-thumbs 预热命令。
    返回 {档位: created/exists/busy/error}，失败不抛异常（调用方只做统计）。"""
    ext = key.rsplit('.', 1)[-1].lower() if '.' in key else ''
    if not _thumb_enabled():
        return {}
    if ext not in ALLOWED_IMAGE_EXTENSIONS or ext in THUMB_NO_PROCESS_EXT:
        return {}
    stats = {}
    pending = []          # [(档位, 缩略图 key)]：真正需要生成的档位，集中一次下载原图后全部产出
    for size in (sizes or THUMB_SIZES):
        thumb_key = s3_thumb_key(key, size)
        try:
            if s3_object_exists(thumb_key):
                stats[size] = 'exists'
                continue
            if not _thumb_lock(thumb_key):
                stats[size] = 'busy'      # 已有人在生成，不重复劳动
                continue
            pending.append((size, thumb_key))
        except Exception as e:
            current_app.logger.warning(f"检查缩略图档位失败 {key} @{size}: {e}")
            stats[size] = 'error'
    if not pending:
        return stats
    try:
        src = _decode_source(key)
    except Exception as e:
        current_app.logger.warning(f"生成缩略图失败（原图不可用）{key}: {e}")
        src = None
    for size, thumb_key in pending:
        try:
            if src is None:
                stats[size] = 'error'
            else:
                s3_put_bytes(_encode_thumb(src, size), thumb_key, 'image/jpeg')
                stats[size] = 'created'
        except Exception as e:
            current_app.logger.warning(f"生成缩略图失败 {key} @{size}: {e}")
            stats[size] = 'error'
        finally:
            _thumb_unlock(thumb_key)      # 无论成败都放锁，失败时下个请求才能立刻重试
    return stats


def build_thumbs_for_url(url, sizes=None):
    """同上，但入参是公网直链（业务表与上传接口手里拿到的都是 URL）；非本桶对象直接忽略"""
    key = s3_key_from_url(url or '')
    if not key:
        return {}
    return build_thumbs_for_key(key, sizes)


@bp.route('/thumb')
def thumb():
    """GET /upload/thumb?s=400&u={原图公网URL} → 302 到缩略图直链（不存在则先生成回写，之后纯走对象存储）。
    任何一步不可用都回原图，绝不让页面出现破图；本路由在 hooks 里免鉴权（见 hooks.before_request）。
    不接受非本桶外链，避免被当成开放重定向跳板。"""
    key = s3_key_from_url(request.args.get('u') or '')
    if not key:
        return jsonify({"code": 404, "msg": "无法识别的图片地址", "data": None}), 404
    try:
        size = int(request.args.get('s') or 0)
    except ValueError:
        size = 0
    ext = key.rsplit('.', 1)[-1].lower() if '.' in key else ''
    if not _thumb_enabled():
        # 开关已关：不能把这次重定向缓存一天，否则下次打开开关后浏览器还始终拿旧地址
        resp = redirect(s3_public_url(key))
        resp.headers['Cache-Control'] = 'no-store'
        return resp
    # 档位不合法 / 不是图片 / 动图：不生成，直接回原图
    if size not in THUMB_SIZES or ext not in ALLOWED_IMAGE_EXTENSIONS or ext in THUMB_NO_PROCESS_EXT:
        return redirect(s3_public_url(key))
    thumb_key = s3_thumb_key(key, size)
    try:
        if not s3_object_exists(thumb_key):
            if not _thumb_lock(thumb_key):
                # 已经有人在生成了：这次先回原图，不排队干等（等对方写完，下个请求就是缩略图）
                return redirect(s3_public_url(key))
            try:
                _make_thumb(key, thumb_key, size)
            finally:
                _thumb_unlock(thumb_key)
    except Exception as e:
        current_app.logger.warning(f"缩略图不可用，回退原图: {key} {e}")
        return redirect(s3_public_url(key))
    resp = redirect(s3_public_url(thumb_key))
    # 302 本身也缓存一天，避免每次重渲染都回到 Flask 绕一圈
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


def _save_image_from_request():
    """通用图片上传处理：校验 + 保存。
    成功返回 (path, filename, size, None)，失败返回 (None, None, None, 错误消息)。
    注意：save_upload_file 返回的是 (路径, file_type, 文件名) 三元组，必须解包！
    只负责落盘，绑定到哪个实体（用户/群/频道）由各路由自己决定。"""
    if 'file' not in request.files:
        return None, None, None, "没有文件"
    file = request.files.get('file')
    size = get_avatar_size(file)
    if size is None:
        return None, None, None, "图片大小限制为10MB"
    result = save_upload_file(file, force_category='avatar', scope=('user', g.user.id))
    if not result:
        return None, None, None, "文件格式不支持或上传失败"
    path, _file_type, filename = result
    return path, filename, size, None

@bp.route('/image', methods=['POST'])
@login_required
def upload_image():
    """通用图片上传（群/频道头像等场景），仅保存文件并返回路径，不改动任何用户数据"""
    try:
        path, filename, size, err = _save_image_from_request()
        if err:
            return jsonify({"code": 400, "msg": err, "data": None}), 400
        return jsonify({
            "code": 0,
            "msg": "上传成功",
            "data": {
                "avatar_url": path
            }
        }), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500


@bp.route('/download', methods=['POST'])
@login_required
def download_file():
    """下载类文件（word/zip 等）按需生成临时签名 URL，并还原真实文件名"""
    data = request.get_json(silent=True) or {}
    url = data.get('url')
    if not url:
        return jsonify({"code": 400, "msg": "缺少 url 参数", "data": None}), 400
    link = s3_presigned_download_url(url, data.get('filename'), expires=3600)
    return jsonify({"code": 0, "msg": "ok", "data": {"download_url": link}})


@bp.route('/avatar',methods=['POST'])
@login_required
def upload_avatar():
    try:
        # 先存新文件，成功后才删旧头像，避免保存失败时旧文件已被删导致头像破图
        path, filename, size, err = _save_image_from_request()
        if err:
            return jsonify({"code": 400, "msg": err, "data": None}), 400
        delete_old_file(g.user.avatar)
        user_id = g.user.id
        file_type = 1
        upload = AvatarFile(user_id=user_id,filename=filename,file_url=path,file_size=size,file_type=file_type)
        # 更新用户头像
        g.user.avatar = path
        db.session.add(upload)
        db.session.commit()
        return jsonify({
            "code": 0,
            "msg": "头像上传成功",
            "data": {
                "avatar_url": path
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"上传失败: {str(e)}", "data": None}), 500




