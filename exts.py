from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_mail import Mail
from flask_caching import Cache
from flask_socketio import SocketIO
db = SQLAlchemy()
mail = Mail()
migrate = Migrate()
cache = Cache()
socketio = SocketIO(cors_allowed_origins="*")  # 支持跨域访问