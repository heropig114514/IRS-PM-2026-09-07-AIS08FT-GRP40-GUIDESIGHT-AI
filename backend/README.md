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
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --lifespan off --ws websockets-sansio --ws-max-size 1048576 --ws-per-message-deflate false
```

仅首次创建 `.env` 时复制模板，避免覆盖自己的配置。已有进程环境变量优先于 `.env`。密钥缺失时启动失败；SQLite 默认文件是 `backend/db.sqlite3`，不会提交。

开发使用 `8766` 端口，以避免与本机已有的 `8765` 服务冲突。

健康检查：<http://127.0.0.1:8766/api/health/>。正常响应为 `{"status":"ok","database":"sqlite"}`；数据库故障返回 503，不重试、不返回连接凭据。

`/` 为手机摄像头回传页；`/ws/echo/` 为单实例内存回传 WebSocket。`/admin/` 为 Django 管理后台，需先创建管理员：

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

`/maps/` 为 Leaflet/OSM 步行路线页，`/maps/config/` 仅返回 ORS 的配置状态，不返回 key。在 `.env` 中配置 `ORS_API_KEY`，重启本应用生效；未配置返回明确的 503。`POST /maps/route/` 接受 `{"start":[经度,纬度],"end":[经度,纬度]}`，要求有效且不同的坐标、严格同源 Origin/Host 和 JSON；不接受额外字段。匿名诊断接口不依赖 Cookie，代理清除会话凭据。返回 `geometry`（GeoJSON LineString）、`summary.distance_m/duration_s`、`steps`（instruction/type/distance_m/duration_s/way_points）和 request_id。ORS 英文原指令保留，前端另显示中文转向类型。

`core/services.py` 固定调用 `api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson`，凭据仅在 HTTPS Authorization 头。单 worker 最多 2 次并发、20 次/分钟；请求 2 KiB、上游响应 1 MiB、网络 I/O 超时 20 秒、客户端请求超时 25 秒。失败明确返回，不重试、不重定向、不生成替代路线。页面、配置和路线响应不缓存；坐标仅处理不持久化，日志不记请求正文、坐标或 key。单元测试 mock 不证明实际 ORS、GPS 或底图已通过。

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

`core/echo.py` 提供二进制原样回传和应用级 ping/pong。每连接 15 秒无输入时关闭；单帧最多 1 MiB、文本最多 1024 字节，进程内最多 4 个连接。只允许明确配置的 Origin 与一致 Host。必须使用一个 Uvicorn worker；上限不在多进程间共享。

`core/camera.py` 从根目录 frontend 的固定白名单读取页面/资源，不暴露任意文件。匿名诊断入口不保存媒体，也不依赖管理后台账号。协议、默认图像参数及客户端测试见 [前端说明](../frontend/README.md)。当前没有业务表、文件存储、队列或产品账号接口。

`core/vision.py` 提供同源检测 HTTP 适配，`core/detector.py` 管理验证后的 CPU ONNX 会话、输入边界、坐标和标注。安装 `requirements-vision.txt`，设置 `YOLO_MODEL_PATH` 为匹配模型清单的绝对路径；开发依赖已包含这些运行库。模型通过独立构建环境导出，服务器不安装训练栈。缺失模型仅影响检测模式，检测请求明确失败；不会自动切换原样回传。见 [VISION.md](VISION.md) 和 [模型工件](models/README.md)。

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

前端另运行 `node --test ../frontend/tests/*.test.mjs`，Node 22+。后端队列测试与前端 socket mock 不验证真实手机权限或摄像头。

## 生产配置边界

显式安装 `requirements-production.txt`，设置 `DJANGO_SETTINGS_MODULE=config.production`，以及模板所列的全部 `DJANGO_ALLOWED_HOSTS` 和 `POSTGRES_*` 变量，再运行迁移与服务。缺失配置或 PostgreSQL 故障不会回退到 SQLite。

当前摄像头服务器使用独立 `config.camera_server`，显式指定本应用 SQLite 数据路径，并通过现有可信同机 Nginx 的 `/prs/` 路由接入 HTTPS；不使用 PostgreSQL 配置。独立系统用户、环境和资源限制见 [部署说明](deploy/README.md)。这属于明确的独立部署配置，不是 PostgreSQL 故障后的回退。

未来正式业务若使用 PostgreSQL，继续显式选择 `config.production`；HTTPS 代理边界确认后才能配置协议头信任。管理后台静态资源需 `manage.py collectstatic --noinput` 并交给 Web 服务器。摄像头部署不公开 `/prs/admin/`，诊断页面不需要管理后台。
