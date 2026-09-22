class BaseConfig:
    SECRET_KEY= 'myChatOnline@2026!!'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

class DevelopmentConfig(BaseConfig):
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:root@127.0.0.1:3306/myChatOnline?charset=utf8'
    # 缓存配置
    CACHE_TYPE = "RedisCache"
    CACHE_REDIS_HOST = "127.0.0.1"
    CACHE_REDIS_PORT = 6379
    # 邮箱配置
    MAIL_SERVER = "smtp.163.com"  # 邮箱服务器，网易是smtp.163.com
    MAIL_USE_SSL = True  # 是否加密传输
    MAIL_PORT = 465  # 若配置了SSL 则端口号为465
    MAIL_USERNAME = "wrm040817@163.com"  # 发用剑所用的用户名
    MAIL_PASSWORD = "WGgVW7FgFhaBmzAW"  # 授权码
    MAIL_DEFAULT_SENDER = "wrm040817@163.com"  # 默认发送者，填写邮箱账号即可
    CACHE_REDIS_PREFIX = " "  # 设置键的前缀 为空 ，默认是"flask_cache_"
    #celery 配置
    CELERY_BROKER_URL = "redis://127.0.0.1:6379/0"
    CELERY_RESULT_BACKEND = "redis://127.0.0.1:6379/0"
