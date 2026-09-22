from flask import Flask
import config
from exts import db, migrate,mail,cache
from models import *
from commands import init_business_role
from blueprints.user import bp as user_bp
from blueprints.upload import bp as upload_bp
from chatonline_celery import make_celery
import hooks
app = Flask(__name__)
app.config.from_object(config.DevelopmentConfig)

celery = make_celery(app)

db.init_app(app)
mail.init_app(app)
migrate.init_app(app, db)
cache.init_app(app)


#设置flask cmd 命令
app.cli.command('init-business-role')(init_business_role)

# 注册蓝图
app.register_blueprint(user_bp)
app.register_blueprint(upload_bp)

# 添加狗子
app.before_request(hooks.before_request)
@app.route('/')
def hello_world():  # put application's code here
    return 'Hello World!'


if __name__ == '__main__':
    app.run()
