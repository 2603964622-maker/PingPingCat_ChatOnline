# ChatOnline（喵喵猫即时通讯平台）

## 一、项目简介

ChatOnline 是一个基于 **Flask + Flask-SocketIO** 构建的全功能即时通讯与社区平台，采用前后端一体的服务端渲染 + AJAX/ WebSocket 混合架构，支持私聊、群聊、频道实时消息、好友系统、朋友圈动态、AI Bot 自动治理、内容安全与后台管理 complete 闭环能力。

## 二、核心功能

### 1. 实时通信
- **私聊**：一对一消息收发、引用回复、消息撤回、文件/图片/视频传输
- **群聊**：群成员角色体系（群主/管理员/成员）、全员禁言、@成员、入群验证、入群申请、群文件、群主转让
- **频道**：帖子发布与评论、回复层级、频道角色与权限、默认副频道主、频道邀请
- 基于 SocketIO 长连接实现消息实时推送，统一会话列表（`user_conversations`）

### 2. 社交体系
- 好友系统（好友/申请/黑名单，`block_type` 通用化拉黑）
- 朋友圈动态（发布、点赞、评论、删除）
- 用户资料卡（头像、签名、性别、可见范围、主页背景图与不透明度、历史背景）
- 在线状态与最近上线时间统计、在线时长统计

### 3. 内容治理与 AI Bot
- 敏感词过滤与违规流水记录（`sensitive_violations`）
- 内置 Bot 模板系统（按 code 绑定配置，如入群自动验证规则）
- 举报与处置流程、用户反馈通道、全站公告系统

### 4. 文件与媒体
- 对象存储（S3 兼容）统一文件存储，UUID 重命名防冲突
- 图片缩略图自动生成与加载策略（Pillow）
- 文件类型 SVG 图标渲染、HTML5 原生视频播放、全屏媒体画廊

### 5. 账号与安全
- 注册/登录/邮箱验证码找回密码（Flask-Mail + Celery 异步发信）
- 单设备登录挤占、登录状态钩子校验
- 管理后台（超管体系、数据统计报表、环比计算、在线人数同源统计）

### 6. 外观与交互
- 深色/浅色主题切换（View Transitions API 动效）、全局自定义背景
- 微信风格移动端适配、安全区域（safe-area）适配
- 长按/右键上下文菜单、圆形光圈与粒子消散动效

## 三、技术栈

### 后端
| 技术 | 用途 |
| --- | --- |
| Python 3.11+ / Flask 3.x | Web 框架与 REST API |
| Flask-SocketIO 5.x + python-socketio | WebSocket 实时通信 |
| SQLAlchemy 2.0 + Flask-SQLAlchemy | ORM 数据访问 |
| Alembic + Flask-Migrate | 数据库迁移（50+ 版本） |
| MySQL (PyMySQL) | 关系型数据库 |
| Redis | 缓存、Celery Broker、在线状态 |
| Celery 5.x（gevent worker） | 异步任务（验证码邮件等） |
| Flask-Caching (cachelib) | 接口/统计缓存 |
| boto3 | S3 兼容对象存储（Sealos / 腾讯云广州） |
| Flask-Mail | 邮件发送 |
| Flask-WTF / itsdangerous | 表单校验与安全签名 |
| Pillow | 图片缩略图生成 |
| gevent | 协程并发（长连接 & worker） |
| python-dotenv | 环境变量管理（.env） |

### 前端
| 技术 | 用途 |
| --- | --- |
| Jinja2 模板 | 服务端渲染 SPA 骨架 |
| 原生 JavaScript | 交互逻辑、AJAX、SocketIO 客户端 |
| CSS3 | 主题、动效（View Transitions、FLIP、毛玻璃 toast） |
| HTML5 Video | 原生视频播放 |
| Socket.IO JS Client | 实时消息收发 |
| particles.js / 自研 sfx.js | 粒子动效与音效 |

### 部署
- **Docker**（python:3.11-slim，`CMD python app.py`）
- **Sealos** 云平台：环境变量注入、后台启动 celery worker、`db.metadata.create_all + flask db stamp head` 初始化数据库
- GitHub 仓库管理，master 分支

## 四、项目结构
ChatOnline/
├── app.py               # 应用入口，注册蓝图 / SocketIO/CLI 命令
├── config.py            # Development/Production 多环境配置
├── exts.py              # db/migrate/mail/cache/socketio 扩展实例
├── decorators.py        # 登录校验等装饰器
├── hooks.py             # before_request 全局钩子
├── presence.py          # 在线状态管理
├── chatonline_celery.py # Celery 工厂
├── commands.py          # 幂等 CLI 初始化命令
├── blueprints/          # 14 个业务蓝图（用户 / 私聊 / 群聊 / 频道 / Bot / 管理后台...）
├── chat/                # SocketIO 事件处理器（private/groups/channels）
├── bots/                # AI Bot 引擎、规则、时间窗口
├── models/              # SQLAlchemy 数据模型（15 个模块）
├── migrations/          # Alembic 迁移版本
├── templates/           # Jinja2 页面模板
└── static/              # CSS / JS / SVG / 图标资源

## 五、架构特点
- **模块化蓝图设计**：每个业务域独立 blueprint，路由、模型、SocketIO 事件三层分离
- **实时 + 轮询混合**：核心消息走 WebSocket，列表/统计走 AJAX + Redis 缓存
- **幂等初始化**：所有 CLI 命令与数据库初始化可安全重复执行，适配容器化部署
- **统一对象存储**：所有文件上传收敛到 S3，路径约定 + UUID 命名 + 本地回滚备份
