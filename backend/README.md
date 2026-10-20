# Backend 开发入口

当前服务推理优化计划、本机基线、独立 Graph 对照及后续条件见 [推理优化计划](INFERENCE_OPTIMIZATION_PLAN.md)。

后续部署目标已选择本机／自有服务器常驻 GPU。服务入口、仓库结构与推理调用链、启动命令及协议见 [常驻 GPU 服务](RESIDENT.md)。页面可显式选择「常驻 GPU」。

测距默认保留 A 双模型，另支持 B 几何测距和 C 低频深度。
配置、A/B/C 运行命令、焦距/类别高度及性能记录见 [DISTANCE-MODES.md](DISTANCE-MODES.md)。

> 本机双模型MVP入口为 http://127.0.0.1:8766/live/ ，使用独立Web/GPU环境与中台适配入口；准备、启动器及验证边界见 [LOCAL-MVP.md](LOCAL-MVP.md)。

同一中台入口额外提供 `/integration/video/`，使用浏览器选定的本地视频文件与独立控制器，仍走 `/ws/detect/local/` 和原双模型。原始公共视频来源、1倍速/两个时钟的限制和Chrome验收命令见 [连续视频说明](LOCAL-MVP.md#连续本地视频入口)，不需要启动电脑摄像头。

街景回放使用MOT17-07白天和MOT17-10夜间移动相机序列，浏览器选择H.264副本。两个原始预览、两个回放副本和来源清单属于版本管理范围，完整标注及运行报告单独管理。素材与标注边界见 [视频数据说明](model_tests/DATASETS.md#连续街景视频用于本地联调)，验收方法见 [本地MVP说明](LOCAL-MVP.md#验收方法与边界)。

所有后端开发在本目录进行。采用 Python 3.12、Django、Django REST Framework 和 Uvicorn，运行依赖版本沿用 AiInterviewer 本地环境；独立实现配置及公共接口，不复制面试业务。

本地模式由 `integration.asgi` 包裹原软件入口，再连接独立的 `integration.inference.asgi`；直接复用原 `ResidentDetector` 双模型链路，不将Torch装入Web环境。HF模式经后端HTTPS/WS转发，无需本地GPU或SSH隧道；配置见 [ZEROGPU.md](ZEROGPU.md)。

可显式启用独立 YOLO26 Depth 模型，将每个检测框对应的有效深度中位数作为米制距离，
并显示在标注 JPEG 中。插入位置、原接口兼容、GPU/Space 配置和验证边界见 [DEPTH.md](DEPTH.md)。
实时路径使用带相机运动补偿的 BoT-SORT，按 ID 平滑距离并输出接近/远离/静止状态。
状态归属、配置、测试、性能和局限见 [TRACKING.md](TRACKING.md)。
运动估计后计算无状态 TTC 与可配置规则风险，返回分数、等级、告警需求和优先级；
公式、完整默认权重、配置及验证边界见 [RISK.md](RISK.md)。算法仅输出风险字段；本地页面Guidance在用户明确启用后消费这些字段并调度共享浏览器语音/振动，不修改评分规则。

Qwen2.5-VL 场景描述（`POST /scene/describe/`）的接口、提示词、配置、部署与限制见 [VLM.md](VLM.md)。
本地模型测试统一放在 [model_tests](model_tests/README.md)，包括运行入口、示例、素材说明和运行输出约定。
本地独立GPU环境从本目录运行 `.\.venv-inference\Scripts\python.exe -X utf8 -m model_tests.run_depth --output model_tests/outputs/bus-run-3`，
默认使用随附公交车图片，保存标注图与 JSONL。公开数据集、建议数量和后续模型对比协议见 [DATASETS.md](model_tests/DATASETS.md)。

## 安装与启动（PowerShell）

完整本地MVP使用 [独立环境与启动器](LOCAL-MVP.md)。以下命令启动原Web软件入口，提供Echo/HF和已有HTTP功能，不启用新的本地推理适配器；其本地模型测试需要另外准备GPU环境。

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
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --workers 1 --lifespan on --ws websockets-sansio --ws-max-size 1310728 --ws-per-message-deflate false
```

仅首次创建 `.env` 时复制模板，避免覆盖自己的配置。已有进程环境变量优先于 `.env`。密钥缺失时启动失败；SQLite 默认文件是 `backend/db.sqlite3`，不会提交。

开发使用 `8766` 端口，以避免与本机已有的 `8765` 服务冲突。

健康检查：<http://127.0.0.1:8766/api/health/>。正常响应为 `{"status":"ok","database":"sqlite"}`；数据库故障返回 503，不重试、不返回连接凭据。

`/` 重定向至 `/live/` 手机摄像头页；`/ws/echo/` 为单实例内存回传 WebSocket。`/admin/` 为 Django 管理后台，需先创建管理员：

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

摄像头支持 Echo、HF ZeroGPU 和常驻 GPU 三个显式模式。原 `config/asgi.py` 媒体路径为
`/ws/echo/` 和 `/ws/detect/hf/`；选择 `config.resident_asgi:application` 启动才增加
`/ws/detect/resident/`。旧 HTTP 检测和旧视频 WS 仍拒绝。`core/detector.py` 只提供共享
JPEG/预处理/框/标注函数，不含CPU推理会话。原模型、640、0.25、JPEG80保持；计算精度和
HF 后端从必需的COCO合格 `models/inference-plan.json` 读取；本机常驻路径保持原 FP32，
采用独立 `resident-v1` 契约，不借用 HF 的精度验证结果。输出数组保持FP32。完整GPU逐项
实验与失败诊断见 [ACCELERATION.md](ACCELERATION.md)，同一个Notebook用版本管理复测。
HF 身份私有配置，开始前检查每日免费秒数和调用次数；一次视频最多55秒，不自动重新分配。

本地集成入口额外提供同源 `/ws/detect/local/`，并转发到独立进程 `/ws/infer/`；
`integration/` 负责契约、会话和进程边界，原 `realtime/` 算法与原HF handler保持原逻辑。
两环境依赖分别由 `requirements-dev.txt` 和 `requirements-inference.txt` 明确安装，
新启动器不安装、下载、迁移、重启或部署。模型健康与Web数据库健康分开记录。

当前仓库未提供训练Notebook。现有本地MVP使用预训练权重，不依赖训练流程，也不会自动替换或部署模型。

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
