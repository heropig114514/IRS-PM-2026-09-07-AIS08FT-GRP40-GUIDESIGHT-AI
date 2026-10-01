# Backend 开发入口

所有后端开发在本目录进行。采用 Python 3.12、Django、Django REST Framework 和 Uvicorn，运行依赖版本沿用 AiInterviewer 本地环境；独立实现配置及公共接口，不复制面试业务。

## 安装与启动（PowerShell）

从仓库根目录开始：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

将生成的值填入 `.env` 的 `DJANGO_SECRET_KEY`，然后执行：

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --lifespan off
```

仅首次创建 `.env` 时复制模板，避免覆盖自己的配置。已有进程环境变量优先于 `.env`。密钥缺失时启动失败；SQLite 默认文件是 `backend/db.sqlite3`，不会提交。

开发使用 `8766` 端口，以避免与本机已有的 `8765` 服务冲突。

健康检查：<http://127.0.0.1:8766/api/health/>。正常响应为 `{"status":"ok","database":"sqlite"}`；数据库故障返回 503，不重试、不返回连接凭据。

`/` 暂无页面，返回 404 是预期结果。`/admin/` 为 Django 管理后台，需先创建管理员：

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

当前 `DEBUG=False`，Uvicorn 不负责静态资源。若需本地操作管理后台，使用 Django 静态资源处理器启动独立开发服务：

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8766 --insecure
```

`--insecure` 仅用于本地静态资源，不用于公网部署。开发两种启动方式时不要占用同一端口。

## 开发结构与接口边界

- `config/base.py`：共享应用装配、会话认证、CSRF、日志。
- `config/settings.py`：本地 SQLite 与 Host 白名单。
- `config/production.py`：独立 PostgreSQL、HTTPS 配置，不回退开发数据库。
- `core/views.py`：公开只读健康检查。
- `core/errors.py`：DRF 标准错误保留原契约；未知错误记录异常栈并返回固定 500。
- `core/tests.py`：隔离数据库及合成故障测试。

API 默认要求登录，仅健康检查显式允许匿名。新业务应用放在本目录，例如执行 `manage.py startapp detection`，再将应用加入 `INSTALLED_APPS`、在 `config/urls.py` 注册路由。按 `models.py`（ORM）、`serializers.py`（契约）、`services.py`（业务规则）、`views.py`（HTTP 适配）划分职责；有实际功能时才创建相应文件。

当前没有业务表、文件存储、推理模型、队列、注册/登录 API 或 WebSocket 路由。Django 管理后台提供管理员登录；后续产品账号接口需独立设计。ASGI 入口为后续扩展保留运行方式，不代表已有实时推理能力。

前端优先通过同源代理访问后端，不开放通配 CORS。会话认证下的业务写请求需有效 CSRF token。接口失败不得隐式重试或改用其他算法；实验参数修改须获得用户明确授权。

## 检查

在 backend 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\ruff.exe check .
```

测试使用临时数据库，不修改本地开发数据库。`.github/workflows/backend.yml` 在 Python 3.12 中执行以上自动检查。当前没有 `tools/check_docs.py`；注释和声明/变量索引需要人工复核，CI 不宣称检查了注释语义。

## 生产配置边界

显式安装 `requirements-production.txt`，设置 `DJANGO_SETTINGS_MODULE=config.production`，以及模板所列的全部 `DJANGO_ALLOWED_HOSTS` 和 `POSTGRES_*` 变量，再运行迁移与服务。缺失配置或 PostgreSQL 故障不会回退到 SQLite。

本框架未包含真实服务器、TLS 或反向代理部署。HTTPS 终止在代理时，需先明确可信代理边界，再配置 `SECURE_PROXY_SSL_HEADER` 等项；当前不信任任意转发协议头。静态资源须先 `manage.py collectstatic --noinput`，然后由 Web 服务器提供。正式部署前运行 `manage.py check --deploy` 并处理部署相关项。
