from exts import db,socketio
from decorators import login_required


#TODO 群消息收发（socketio.on 接收，入库，emit 广播房间，鉴权取用户身份）