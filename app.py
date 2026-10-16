from dotenv import load_dotenv
load_dotenv()  # 必须在 import config 之前，让 os.environ 先就绪

from flask import Flask, render_template
import config
from exts import db, migrate,mail,cache,socketio
from models import *
from commands import init_business_role, import_sensitive_words, init_bots, warm_thumbs
# 导入蓝图
from blueprints.user import bp as user_bp
from blueprints.upload import bp as upload_bp
from blueprints.private import bp as private_bp
from blueprints.search import bp as search_bp
from blueprints.channels import bp as channels_bp
from blueprints.groups import bp as groups_bp
from blueprints.friends import bp as friends_bp
from blueprints.moments import bp as moments_bp
from blueprints.notifications import bp as notifications_bp
from blueprints.bot import bp as bot_bp
from blueprints.report import bp as report_bp
from blueprints.feedback import bp as feedback_bp
from blueprints.announcement import bp as announcement_bp
from blueprints.admin import bp as admin_bp


from chatonline_celery import make_celery
import hooks
app = Flask(__name__)
app.config.from_object(config.DevelopmentConfig)

celery = make_celery(app)

db.init_app(app)
mail.init_app(app)
migrate.init_app(app, db)
cache.init_app(app)
socketio.init_app(app)


#设置flask cmd 命令
app.cli.command('init-business-role')(init_business_role)
# 部署后初始化敏感词库：从 sensitive_words.txt 导入
app.cli.command('import-sensitive-words')(import_sensitive_words)
# 部署后初始化内置 Bot 模板（幂等，按 code 去重）
app.cli.command('init-bots')(init_bots)
# 给存量图片一次性补齐缩略图：flask warm-thumbs [--limit 50]（幂等，可反复重跑）
app.cli.command('warm-thumbs')(warm_thumbs)

# 注册蓝图
app.register_blueprint(user_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(private_bp)
app.register_blueprint(search_bp)
app.register_blueprint(channels_bp)
app.register_blueprint(groups_bp)
app.register_blueprint(friends_bp)
app.register_blueprint(moments_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(bot_bp)
app.register_blueprint(report_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(announcement_bp)
app.register_blueprint(admin_bp)

# 添加钩子
app.before_request(hooks.before_request)

# 注册 SocketIO 事件处理器（必须在 socketio.init_app 之后导入，导入即触发 @socketio.on 注册）
import chat.private
import chat.groups
import chat.channels

@app.route('/')
def index():
    # 未登录会被 before_request 钩子重定向到登录页；这里只服务主聊天 SPA
    return render_template('chat/app.html')


if __name__ == '__main__':
    # 容器/云部署必须绑 0.0.0.0；allow_unsafe_werkzeug 是 Flask-SocketIO 5.x 在非本地 host
    # 使用开发服务器的强制确认开关，不设会拒绝启动。后续如换 gunicorn 可去掉。
    socketio.run(
        app,
        host='0.0.0.0',
        port=5000,
        allow_unsafe_werkzeug=True,
    )
