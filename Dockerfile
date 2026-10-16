# 指定基础镜像：Python 3.11 的精简版，包含运行环境
FROM python:3.11-slim

# 设置工作目录：后续指令都在这个目录下执行
WORKDIR /app

# 先复制依赖文件（利用 Docker 缓存，依赖不变就不重装）
COPY requirements.txt .

# 安装依赖：--no-cache-dir 不保留下载缓存，减小镜像体积
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目所有代码到工作目录
COPY . .

# 声明容器监听的端口（文档说明，实际映射在 Sealos 界面填）
EXPOSE 5000

# 容器启动命令：运行 app.py
CMD ["python", "app.py"]