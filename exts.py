from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_mail import Mail
from flask_caching import Cache
from flask_socketio import SocketIO
from flask import current_app
import boto3
from botocore.config import Config as BotoConfig
#-------------------------------------------------------
db = SQLAlchemy()
mail = Mail()
migrate = Migrate()
cache = Cache()
socketio = SocketIO(cors_allowed_origins="*")  # 支持跨域访问

def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=current_app.config['S3_ENDPOINT_URL'],
        aws_access_key_id=current_app.config['S3_ACCESS_KEY'],
        aws_secret_access_key=current_app.config['S3_SECRET_KEY'],
        region_name=current_app.config.get('S3_REGION', 'local'),
        # 强制 path 路径式寻址：Sealos 广州节点域名证书只覆盖单级 *.gzg.sealos.run，
        # 默认的 auto/virtual 会把桶名拼到域名前导致 NoSuchBucket / SSL 校验失败
        config=BotoConfig(signature_version='s3v4', s3={'addressing_style': 'path'}),
    )


def s3_public_base_url():
    """桶的公网访问前缀：{endpoint}/{bucket}"""
    endpoint = current_app.config['S3_ENDPOINT_URL'].rstrip('/')
    return f"{endpoint}/{current_app.config['S3_BUCKET']}"


def s3_public_url(key):
    """由对象 key 拼出可直接渲染的公网 URL"""
    return f"{s3_public_base_url()}/{key}"


def s3_key_from_url(url_or_key):
    """从完整公网 URL 反推出 key；已是 key 则原样返回；外部无关 URL 返回 None"""
    if not url_or_key:
        return None
    base = s3_public_base_url()
    if url_or_key.startswith(base + '/'):
        return url_or_key[len(base) + 1:]
    if not url_or_key.startswith('http'):
        return url_or_key.lstrip('/')
    return None


def s3_upload_fileobj(fileobj, key):
    """上传文件流到对象存储，按扩展名自动设置 ContentType，成功返回公网 URL"""
    import mimetypes
    extra_args = {}
    ctype = mimetypes.guess_type(key)[0]
    if ctype:
        extra_args['ContentType'] = ctype
    get_s3_client().upload_fileobj(fileobj, current_app.config['S3_BUCKET'], key, ExtraArgs=extra_args)
    return s3_public_url(key)


# 缩略图档位（长边像素）：400 给网格/列表/气泡，900 给单图大图位（列表里单图最大只渲染到 380px 宽，
# 再大的档位只是白耗流量与首次生成时间）。
# 缩略图只是原图的派生副本（{原图 key}.t{边长}.jpg，同目录），原图字节永不改动。
THUMB_SIZES = (400, 900)
# 已停用的历史档位：生图时不再用，但删原图时要连带清理，否则桶里留下清不掉的孤儿副本
THUMB_SIZES_LEGACY = (1280,)


def s3_thumb_key(key, size):
    """原图 key -> 该档缩略图 key"""
    return f"{key}.t{size}.jpg"


def s3_object_exists(key):
    """对象是否已存在：head 404 视为不存在，其余异常照抛由调用方兜底"""
    from botocore.exceptions import ClientError
    try:
        get_s3_client().head_object(Bucket=current_app.config['S3_BUCKET'], Key=key)
        return True
    except ClientError as e:
        code = str(e.response.get('Error', {}).get('Code', ''))
        if code in ('404', 'NoSuchKey', 'NotFound'):
            return False
        raise


def s3_get_bytes(key):
    """把对象整体读进内存，仅用于缩略图这类小对象（调用方需自行限制原图体积）"""
    return get_s3_client().get_object(Bucket=current_app.config['S3_BUCKET'], Key=key)['Body'].read()


def s3_put_bytes(data, key, content_type, max_age=31536000):
    """写入小体积派生对象（缩略图等）；名字里带原图 UUID，内容永不变，故给一年强缓存"""
    get_s3_client().put_object(
        Bucket=current_app.config['S3_BUCKET'], Key=key, Body=data,
        ContentType=content_type,
        CacheControl=f"public, max-age={max_age}, immutable",
    )
    return s3_public_url(key)


def s3_delete_object(url_or_key):
    """按 URL 或 key 删除对象；无法识别为桶内对象则返回 False。
    一并清掉派生的缩略图副本，避免桶里留下孤儿对象（没生成过缩略图属正常，静默忽略）"""
    key = s3_key_from_url(url_or_key)
    if not key:
        return False
    client = get_s3_client()
    bucket = current_app.config['S3_BUCKET']
    client.delete_object(Bucket=bucket, Key=key)
    for size in THUMB_SIZES + THUMB_SIZES_LEGACY:
        try:
            client.delete_object(Bucket=bucket, Key=s3_thumb_key(key, size))
        except Exception:
            pass
    return True


def s3_presigned_download_url(url_or_key, filename=None, expires=3600):
    """为下载类文件生成临时签名 URL，并强制下载时还原真实文件名"""
    from urllib.parse import quote
    key = s3_key_from_url(url_or_key)
    if not key:
        return url_or_key
    real_name = filename or key.rsplit('/', 1)[-1]
    disposition = f"attachment; filename*=UTF-8''{quote(real_name)}"
    return get_s3_client().generate_presigned_url(
        'get_object',
        Params={
            'Bucket': current_app.config['S3_BUCKET'],
            'Key': key,
            'ResponseContentDisposition': disposition,
        },
        ExpiresIn=expires,
    )