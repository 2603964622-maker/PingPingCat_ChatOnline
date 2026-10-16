import os
#---------------------------------------------------------------------------------------------------------------
#Flask 配置项分类原则
    #敏感或可能随环境变动的配置（密钥、数据库/Redis/S3 地址与密码、第三方 API Key）统一走环境变量，称为外围配置项；固定不变的业务配置
    # （缓存类型、超时时间、功能开关）直接写死在 config.py 里。读取值时一律用 os.environ.get() 加默认值兜底，避免遗漏导致启动崩溃。
#---------------------------------------------------------------------------------------------------------------
class DevelopmentConfig:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'myChatOnline@2026!!')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'mysql+pymysql://root:root@127.0.0.1:3306/myChatOnline?charset=utf8'
    )
    # 缓存配置
    CACHE_TYPE = "RedisCache"
    CACHE_REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
    CACHE_REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
    # 邮箱配置
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.163.com")  # 邮箱服务器，网易是smtp.163.com
    MAIL_USE_SSL = True  # 是否加密传输
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 465))  # 若配置了SSL 则端口号为465
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "wrm040817@163.com")  # 发信邮箱账号
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "VSfSGB6MceURdiJN")  # 邮箱授权码，务必用环境变量注入，切勿硬编码
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)  # 默认发送者
    CACHE_REDIS_PREFIX = " "  # 设置键的前缀 为空 ，默认是"flask_cache_"
    # celery 配置
    _REDIS_URL = f"redis://{CACHE_REDIS_HOST}:{CACHE_REDIS_PORT}/0"
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", _REDIS_URL)
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", _REDIS_URL)
    # 对象存储 (Sealos S3 兼容)：国内广州节点，文件统一上传对象存储，不再落本地 static
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "https://objectstorageapi.gzg.sealos.run")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "6730hk8i")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "fzmtflgplc4p75k2")
    S3_BUCKET = os.environ.get("S3_BUCKET", "6730hk8i-ping-ping-cat")
    S3_REGION = os.environ.get("S3_REGION", "local")
    # 管理端注册邀请密钥：配置后 /admin/register 必须携带匹配值；留空则开放注册（仅建议本地开发）
    # 首个注册的管理员自动成为超级管理员（role=1）
    ADMIN_SIGNUP_KEY = os.environ.get("ADMIN_SIGNUP_KEY")
    # 上传后“同步”生成缩略图体积上限（字节）：生成要先从桶里把原图拉回服务器，
    # 开发机到桶的上行吞吐实测只有一二十 KB/s，一张 4MB 手机照会把上传接口拖到几分钟；
    # 故只对小体积（头像、普通截图）同步做，大文件交给 Celery 与按需生成。
    # 生产容器与桶同区域，带宽充裕，可把 THUMB_SYNC_MAX_BYTES 环境变量调到 5MB 做到“发布必有图”。
    THUMB_SYNC_MAX_BYTES = int(os.environ.get("THUMB_SYNC_MAX_BYTES", 400 * 1024))
    # 缩略图总开关：开发机到桶的上行实测只有一二十 KB/s，现生成一张要 70~130 秒，
    # 本地调试时把 THUMB_ENABLED=0 注到 .env 里，列表就直接显示原图（浏览器直连对象存储，反而更快）；
    # 生产容器与桶同区域，保持默认开启以省流量。关掉后上传也不会再浪费时间去派生副本。
    THUMB_ENABLED = os.environ.get("THUMB_ENABLED", "1") == "1"
#---------------------------------------------------------------------------------------------------------------
class ProductionConfig:
    #相关系统配置
    # 注：统一用 .get 读取，避免开发机未设这些变量时 import config 直接 KeyError 崩溃；
    # 生产环境照常注入环境变量即可，缺失时值为 None，会在实际使用时暴露而非启动即崩
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("SQLALCHEMY_DATABASE_URI")
    # 邮箱配置
    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "True") == "True"
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 465))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER")
    # redis 缓存配置
    CACHE_TYPE = os.environ.get("CACHE_TYPE","RedisCache")  # 如果环境变量中没有，就用RedisCache作为替代
    CACHE_REDIS_HOST = os.environ.get("CACHE_REDIS_HOST", "127.0.0.1")
    CACHE_REDIS_PORT = int(os.environ.get("CACHE_REDIS_PORT", 6379))
    CACHE_REDIS_PASSWORD = os.environ.get("CACHE_REDIS_PASSWORD") or None  # Sealos Redis 有密码时必须填
    # celery配置 - 如果设了密码，强制用拼好的带密码 URL（覆盖 Sealos 自动注入的无密码 CELERY_BROKER_URL）
    _pwd = f":{CACHE_REDIS_PASSWORD}@" if CACHE_REDIS_PASSWORD else ""
    _REDIS_URL = f"redis://{_pwd}{CACHE_REDIS_HOST}:{CACHE_REDIS_PORT}/0"
    CELERY_BROKER_URL = _REDIS_URL if CACHE_REDIS_PASSWORD else os.environ.get("CELERY_BROKER_URL", _REDIS_URL)
    CELERY_RESULT_BACKEND = _REDIS_URL if CACHE_REDIS_PASSWORD else os.environ.get("CELERY_RESULT_BACKEND", _REDIS_URL)
    #对象存储
    # 对象存储配置 (Sealos S3 兼容)
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY")
    S3_BUCKET = os.environ.get("S3_BUCKET")
    S3_REGION = "local"  # Sealos 一般不强制 region，填 local 或任意值即可
    # 管理端注册邀请密钥：生产务必通过环境变量注入，否则管理端将对任何人开放注册
    # 首个注册的管理员自动成为超级管理员（role=1）
    ADMIN_SIGNUP_KEY = os.environ.get("ADMIN_SIGNUP_KEY")