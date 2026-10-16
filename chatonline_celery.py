from flask_mail import Message
from exts import mail
from celery import Celery


# 定义任务函数
def send_mail(recipient, subject, body):
    message = Message(subject=subject, recipients=[recipient], body=body)
    mail.send(message)
    print("发送成功")


def build_thumbs(url):
    """上传完成后异步把各档缩略图备好，把“第一次看也慢”从用户身上挪走。
    延迟导入 blueprints.upload：本模块在应用创建早期就被调用，且 worker 里也不需要路由对象。"""
    from blueprints.upload import build_thumbs_for_url
    return build_thumbs_for_url(url)


def make_celery(app):
    celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'],
                    backend=app.config['CELERY_RESULT_BACKEND'])
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    app.celery = celery
    # 添加任务
    celery.task(name="send_mail")(send_mail)
    celery.task(name="build_thumbs")(build_thumbs)
    return celery