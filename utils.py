import os
import uuid
from datetime import datetime
from flask import current_app,jsonify,g
from models.user import User
from models.group import Group
from models.channel import Channel
from models.message import Conversation
import random
from exts import db


 # 1. 工具函数中不要用jsonify ，因为工具函数中不涉及响应，只涉及数据处理，return None即可
 # 2. 一个工具函数只有一个功能，不可设计多个功能，例如上传文件函数就只做"上传文件返回路由"这一件事情
 # 举例说明:
 #        工具函数中出错了"return None"也只是跳出了工具函数体，还是会继续执行路由函数的，假设我们给上传文件的工具函数设置
 #        检查文件大小，查出100MB就return& 上传 的两种功能，首先上传之前需要先检查文件大小，超出返回None,文件还没有上传
 #        那我们后端返回的json数据该怎么写呢?很显然，没有办法写;所以我们我们书写两个功能函数，分别是:
 #          函数1:检查文件大小是否超出文件限制,超出return None
 #          函数2:上传文件并返回路由
 #        这样的话我们后端的路由函数可以根据这个工具函数return None 来写不同的JSON信息了
#-------------------------------------------------------------------------------------------------------------
# 关于函数注释
# def function(形参):
#   """
#    用自然语言说明这个函数做什么、为什么存在（不是怎么实现）
#   :param type:描述函数所用形参类型
#   :return:返回值是什么,返回值异常/正确也要写
#   """
#--------------------------------------------------------------------------------------------------------------
def generate_unique_number(type):
    """
    根据 形参 type 生成带前缀的、不同种类的唯一编号 【用户、群聊、频道号】
    :param type: 字符串前缀 type,例如 "user_", "group_", "channel_"
    :return: type形参 + 随机生成的字符串
    """
    POOL = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
    while True:
        # 每次生成纯数字，并拼上前缀
        digits = "".join(random.sample(POOL, k=10))
        full_number = type + digits
        # 按类型查对应表（判断查到的结果是否为空）
        if type == "user_":
            exists = User.query.filter_by(number=full_number).first()
        elif type == "group_":
            exists = Group.query.filter_by(number=full_number).first()
        elif type == "channel_":
            exists = Channel.query.filter_by(number=full_number).first()
        else:
            # 非法类型，直接返回 None 或抛异常
            return None
        # 核心：没查到才退出，查到了就继续循环重生成
        if not exists:
            break
    return full_number
#--------------------------------------------------------------------------------------------------------------
# 头像允许的文件扩展名
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mkv', 'mov', 'wmv', 'flv'}

def allowed_file_extend(filename,allowed_set):
    """ 检查头像文件扩展名是否合法 """
    return  filename.rsplit('.',1)[1].lower() in allowed_set
#--------------------------------------------------------------------------------------------------------------
def generate_filename(original_filename):
    """
    生成唯一文件名
    格式：时间戳-uuid.扩展名
    """
    ext = original_filename.rsplit('.', 1)[1].lower()
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    # datetime.now():获取当前的时间【比如：2026年09月20日 19时56分15秒】
    # .strftime:时间对象的“格式化输出”方法
    unique_id = uuid.uuid4().hex[:8]
    # 默认的uuid是这样:7dec-11d0-a765-00a0c91e6bf6（带横杠，很长）
    # .hex:UUID对象属性，会把上面的标准UUID去掉横杠，只保留字符串
    # [:8]:Python字符串切片，只要这个字符串前八个字符
    return f"{timestamp}_{unique_id}.{ext}"
#--------------------------------------------------------------------------------------------------------------
def save_upload_file(file, force_category=None):
    """
    根据文件扩展名自动判断类型并保存到对应目录
    图片 → static/photo/ (file_type=3)
    视频 → static/video/ (file_type=2)
    其他 → static/other/ (file_type=4)
    头像 → static/avatar/ (file_type=1)

    :param file: request.files['file'] 传进来的文件对象
    :param force_category: 强制指定类型，'avatar' 时走头像逻辑
    :return: (相对路径, file_type) 成功；失败返回 (None, None)
    """
    if not file or not file.filename:
        return None
    # 头像特殊处理
    if force_category == 'avatar':
        folder = os.path.join(current_app.root_path, 'static', 'avatar')
        allowed = ALLOWED_IMAGE_EXTENSIONS
        if not allowed_file_extend(file.filename, allowed):
            return None
        os.makedirs(folder, exist_ok=True)
        filename = generate_filename(file.filename)
        file.save(os.path.join(folder, filename))
        return f"/static/avatar/{filename}", 1

    # 自动判断类型
    ext = file.filename.rsplit('.', 1)[1].lower()

    if ext in ALLOWED_IMAGE_EXTENSIONS:
        folder_name = 'photo'
        file_type = 3
    elif ext in ALLOWED_VIDEO_EXTENSIONS:
        folder_name = 'video'
        file_type = 2
    else:
        folder_name = 'other'
        file_type = 4

    folder = os.path.join(current_app.root_path, 'static', folder_name)
    os.makedirs(folder, exist_ok=True)
    filename = generate_filename(file.filename)
    file.save(os.path.join(folder, filename))

    return f"/static/{folder_name}/{filename}", file_type,filename
#--------------------------------------------------------------------------------------------------------------
def delete_old_file(relative_path):
    """
    删除static下的旧文件
    :param relative_path: 数据库里存的相对路径，
    如 '/static/avatar/xxx.jpg' 或 'static/avatar/xxx.jpg
    """
    if not relative_path or relative_path.startswith('http'):
        #.startswith() 是Python字符串方法，用来判断一个字符串是否以某个前缀开头
        return
    path = relative_path.lstrip('/')
    abs_path = os.path.join(current_app.root_path, path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
#--------------------------------------------------------------------------------------------------------------
def get_file_size(file):
    """
    获取上传文件的字节大小
    :param file: request.files.get('file')拿到的 FileStorage对象
    :return: 文件大小（字节），失败返回 None
    """
    # 设置文件单次上传限制
    MAX_FILE_SIZE_MB = 100
    file.seek(0,os.SEEK_END)  #seek(位置,参考点): 移动文件读写指针  os.SEEK_END 表示文件末尾
    size = file.tell()     # 返回当前指针所在的位置(字节数)
    file.seek(0)            # 回到文件开头
    file_size_MB = size // (1024*1024)   # 用/得到的是浮点数，直接用 // 是整除，丢失小数
    if file_size_MB > MAX_FILE_SIZE_MB:
        return None
    return file_size_MB
    # 关于seek和tell是Python文件类对象的通用方法，只要一个对象长得像文件，就有这俩方法
#--------------------------------------------------------------------------------------------------------------
def get_avatar_size(file):
    MAX_FILE_SIZE_MB = 2
    file.seek(0,os.SEEK_END)
    size = file.tell()
    file.seek(0)
    file_size_MB = size // (1024*1024)
    if file_size_MB > MAX_FILE_SIZE_MB:
        return None
    return file_size_MB
#--------------------------------------------------------------------------------------------------------------
PARAMS_ERROR = 400
SERVER_ERROR = 500
OK = 0
def resp_json(code=OK,msg="",data=None):
    return jsonify(code=code,msg=msg,data=data)
#---------------------------------------------------------------------------------------------------------------