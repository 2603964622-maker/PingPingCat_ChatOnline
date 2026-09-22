import random
import os
import uuid
from datetime import datetime
from flask import current_app
from models.user import User

def generate_unique_number():
    POOL=['0','1','2','3','4','5','6','7','8','9']
    number = "".join(random.sample(POOL, 10))
    status=True
    while status:
        if User.query.filter_by(number=number).first():
           number = "".join(random.sample(POOL, 10))
        else:
            status = False
    return number

######################################################
# 允许的文件扩展名
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'webm'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'}
ALLOWED_DOCUMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md'}
ALLOWED_ARCHIVE_EXTENSIONS = {'zip', 'rar', '7z', 'tar', 'gz'}

def allowed_file(filename,allowed_set):
    """ 检查文件扩展名是否合法 """
    return '.' in filename and \
        filename.rsplit('.',1)[1].lower() in allowed_set


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


def save_upload_file(file, category='avatar'):
    """
    保存上传的文件到指定目录
    :param file: request.files['file'] 传进来的文件对象
    :param category: 'avatar' | 'picture' | 'media' | 'audio' | 'document' | 'archive'
    :return: 成功返回相对路径字符串，失败返回 None
    """
    if not file or file.filename == '':
        return None

    # 根据类型选择目录和允许的扩展名
    if category == 'avatar' or category == 'picture':
        folder = os.path.join(current_app.root_path, 'static', 'avatar' if category == 'avatar' else 'picture')
        allowed = ALLOWED_IMAGE_EXTENSIONS

    elif category == 'media':
        folder = os.path.join(current_app.root_path, 'static', 'media')
        allowed = ALLOWED_VIDEO_EXTENSIONS

    elif category == 'audio':
        folder = os.path.join(current_app.root_path, 'static', 'audio')
        allowed = ALLOWED_AUDIO_EXTENSIONS

    elif category == 'document':
        folder = os.path.join(current_app.root_path, 'static', 'document')
        allowed = ALLOWED_DOCUMENT_EXTENSIONS

    elif category == 'archive':
        folder = os.path.join(current_app.root_path, 'static', 'archive')
        allowed = ALLOWED_ARCHIVE_EXTENSIONS

    else:
        return None

    # 检查扩展名
    if not allowed_file(file.filename, allowed):
        return None

    os.makedirs(folder, exist_ok=True)

    # 生成安全且唯一的文件名
    filename = generate_filename(file.filename)

    # 保存文件
    file_path = os.path.join(folder, filename)
    file.save(file_path)

    # 返回相对路径（存数据库用这个）
    category_folder_map = {
        'avatar': 'avatar',
        'picture': 'picture',
        'media': 'media',
        'audio': 'audio',
        'document': 'document',
        'archive': 'archive'
    }
    folder_name = category_folder_map.get(category, 'avatar')
    return f"/static/{folder_name}/{filename}"
###################################################
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
################################################################
def get_file_size(file):
    """
    获取上传文件的字节大小
    :param file: request.files.get('file')拿到的FileStorage 对象
    :return: 文件大小（字节），失败返回None
    """
    if not file:
        return None
    file.seek(0,os.SEEK_END)  #seek(位置,参考点): 移动文件读写指针  os.SEEK_END 表示文件末尾

    size = file.tell()     # 返回当前指针所在的位置(字节数)
    file.seek(0)  # 回到文件开头
    size_mb = size / (1024*1024)   # 用/得到的是浮点数，直接用 // 是整除，丢失小数
    return size_mb
    # 关于seek和tell是Python文件类对象的通用方法，只要一个对象长得像文件，就有这俩方法