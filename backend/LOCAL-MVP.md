# 本机 MVP 集成与部署

本阶段在一台电脑完成摄像头/本地视频、算法结果、浏览器提醒及导航示例的集成。中台只适配协议、
进程和会话生命周期，不修改检测/深度模型、跟踪关联、距离运动估计、TTC或风险规则。
HF、ORS和DashScope不参与这条本地推理闭环；公网模式配置见 [ZEROGPU.md](ZEROGPU.md)，部署方式见 [运维说明](deploy/README.md)。

## 运行边界

```text
浏览器 http://127.0.0.1:8766/live/
  └─ Web/中台进程：backend/.venv
       ├─ integration.asgi → 原 Django、Echo、控制 WS和输出 MCP
       └─ 同源本地检测 WS → ws://127.0.0.1:8767/ws/infer/
            └─ 推理进程：backend/.venv-inference
                 └─ ResidentDetector → YOLO26n → BoT-SORT → YOLO26 Depth
                    → 距离运动状态 → TTC/规则风险 → 标注 JPEG和对象元数据
```

两个进程各使用单 worker；Web环境不安装Torch，GPU环境不需要Django、MCP、Gradio或
Space账户。原 `realtime/` 算法由推理适配器直接调用；每个视频会话隔离 tracker/history，
模型和Graph复用不等于跨会话复用目标身份。浏览器的Guidance模块消费服务端告警字段，
不重新计算风险。软件原有Echo/HF入口和外部API实现保留，本地启动明确关闭其外部凭据。

新集成入口是 `integration.asgi:application`，原 `config.asgi:application` 可独立运行，
不因此拥有本地推理路径。推理入口是 `integration.inference.asgi:application`。
两进程只监听 `127.0.0.1`；默认Web8766、推理8767，可在启动器显式选择不同端口。

## 环境与模型准备

从仓库根目录进入 `backend`，使用Python3.12。Windows本机可用基础解释器为
`C:\Users\Kolosin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`；
其它电脑替换为自己的Python3.12路径。已有环境时跳过创建步骤，不覆盖其它项目环境。

```powershell
cd backend
$localPython = 'C:\Users\Kolosin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $localPython -m venv .venv
& $localPython -m venv .venv-inference

# Web与开发检查；不安装原生CUDA依赖。
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt --retries 0

# 本机RTX5070Ti Laptop选择官方CUDA12.8 wheel，基础版本保持原2.8.0/0.23.0。
.\.venv-inference\Scripts\python.exe -m pip install -r requirements-inference.txt torch==2.8.0+cu128 torchvision==0.23.0+cu128 --extra-index-url https://download.pytorch.org/whl/cu128 --retries 0 --timeout 60

.\.venv\Scripts\python.exe -m pip check
.\.venv-inference\Scripts\python.exe -m pip check
```

`requirements-inference.txt` 独立固定原生推理依赖；不与用于离线导出的
`requirements-vision-build.txt`（Torch2.5.1/Torchvision0.20.1）混装。
`requirements-depth.txt` 仍用于原有组合模型测试，不作为新的纯GPU服务环境清单。
Torch官方版本命令见 [PyTorch previous versions](https://pytorch.org/get-started/previous-versions/)。
本机显卡为NVIDIA RTX5070Ti Laptop GPU、12GB显存；显存容量本身不证明模型吞吐或时延达标。

将已明确下载的官方 `v8.4.0` 权重放在 `backend/models/`。运行服务时不会自动下载或换模型。

| 文件 | 官方来源 | 权重SHA256 |
|---|---|---|
| `yolo26n.pt` | [Ultralytics release](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt) | `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef` |
| `yolo26n-depth.pt` | [Ultralytics release](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-depth.pt) | `befed1b8561d8b2eaa66274b070cdfc9c44853bda6d6d62a04759f9383af74e9` |

```powershell
Get-FileHash -Algorithm SHA256 models/yolo26n.pt, models/yolo26n-depth.pt
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --check
```

`--check` 检查两个解释器的Python版本、直接依赖pin、实际CUDA可用性、模型SHA与端口，
不写配置、不启动服务、不执行模型预测。检查通过不等于真实双模型或浏览器已验证。
缺包、CUDA不可用、权重缺失/不匹配及占用端口都会明确失败，无CPU或单检测降级。
权重及两个venv均Git忽略；官方模型/Ultralytics的许可边界见 [models/README.md](models/README.md)。

## 启动与停止

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp

# 如需将两子进程日志保存到被忽略的venv目录：
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --log-dir .venv/tmp/local-mvp

# 明确使用其它回环端口，不自动寻找空闲端口：
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --web-port 8776 --inference-port 8777
```

启动器只创建/读取私有 `.env.local-mvp` 中的随机 `DJANGO_SECRET_KEY`，不覆写 `.env`，
不打印密钥。它向子进程显式设置 `HF_TOKEN/HF_INFERENCE_TOKEN/ORS_API_KEY/DASHSCOPE_API_KEY`
为空，避免 `config.base` 读取现有 `.env` 时隐式启用外部服务；设置
`DJANGO_SETTINGS_MODULE=config.settings` 和 `PRS_MCP_PROFILE=outputs`。
Web默认SQLite仍位于 `backend/db.sqlite3`，本地MVP不依赖业务账号或数据库写入。
启动器不自动安装、下载、迁移、部署或重启失败进程。

GPU子进程显式设置 `YOLO_OFFLINE=1`，使固定Ultralytics版本在初始化在线状态时直接返回
offline，不执行其Cloudflare/Google DNS探测；设置 `YOLO_AUTOINSTALL=0` 禁止库自动补包。
`YOLO_CONFIG_DIR` 指向 `backend/.venv-inference`，库设置写入其 `Ultralytics/` 子目录，
不使用用户的Roaming配置。`--check` 使用同一环境配置，但仅查询包元数据并导入Torch，
不会导入Ultralytics或创建该设置目录。这些是运行环境隔离，不修改算法实现或实验参数。

GPU先启动，Web随后启动；各自最多55秒观察监听，再做一次HTTP健康检查，不发送测试帧。
`/health/` 是GPU进程状态，`/integration/status/` 是Web对该状态的代理。
首次 `loaded=false/ready=false` 是正常冷启动，表示尚未进行首帧模型加载；
`accept_available` 表示可接收会话，不能把监听或HTTP200当作推理已经成功。
数据库健康 `/api/health/` 与推理健康是不同检查。

默认前台运行，子进程输出进入当前终端；Windows子进程不弹出窗口。按Ctrl+C停止本启动器
持有的两个子进程，先Web后GPU；等待10秒后仍未退出则只终止同一个子进程句柄。
不根据进程名称或持久化PID查杀，不影响其它项目。可选日志保存在每次独立子目录。

## 本地操作与契约

打开打印出的 `/live/` 页面，读取并明确选择本机摄像头。首次设备ID为空时，点击
“授权并读取摄像头列表”：只申请video权限、立即停止临时tracks，不启动算法或自动选设备。
授权后仍需明确选设备，使用本地推理模式再点击原开始按钮；设备消失时不自动切换。
默认保持检测640、
confidence0.25、JPEG80；原生检测FP32、深度768/FP32。本地请求为PRF2（序号、浏览器采集秒数、
原JPEG），结果复用PRS1；原HF的PRF1协议保持。JPEG1MiB、JSON256KiB，结果总包1310728字节；
顺序、严格递增的采集时间、图像SHA与所选模式的实际计算身份均校验。适配层把采集时间传给
已有 `predict(..., timestamp=...)`，不改算法估速规则；时间不与另一机器时钟相减。
原HF编译FP16的COCO证据不被当成本机原生FP32双模型性能证明。

调度和故障预算保持原30FPS输入上限、12帧在途、55秒会话、55秒首帧和10秒后续等待；
worker5秒无输入结束。输入上限不是实测输出FPS，故障等待预算也不是500ms验收阈值。
停止/断开必须结束当前会话并释放在途工作；新会话清空tracker/history，不自动重连或重试。
首帧包含权重加载、模型初始化和Graph准备，应与后续帧分别记录。

用户启用Guidance提醒后，浏览器按服务端 `alert_required/alert_priority/risk_level` 选择输出，
语音和振动仍受各自用户开关、前台状态和设备API限制。具体调度、去重及导航示例操作以
[前端说明](../frontend/README.md)为准；关闭提醒、停止推流或页面隐藏后停止相关输出。
语音的提交/引擎start事件不证明用户听到了实际声音，振动API接受也不证明马达震动。
桌面本地阶段优先验证协议和语音引擎事件；真手机马达、扬声器、权限和移动网络另行验收。

导航示例仅用于演示导航事件与风险提醒的仲裁，不是GPS定位、ORS路线或实际导航准确率验证。
地图/场景描述仍是可选外部API；本地集成入口在调用原handler之前拒绝HF/ORS/DashScope路径，
启动器也清空其凭据，
不能把本地推理闭环说成地图/视觉大模型也已离线运行。

## 连续本地视频入口

独立入口为 `http://127.0.0.1:8766/integration/video/`，随同一启动器运行；选择电脑上的
视频文件，再明确点击开始。页面用File/Blob与HTMLVideo在浏览器本地解码，不申请电脑
摄像头权限、不使用fake camera，不向服务器上传整个视频文件。只有实际采集的JPEG与
原PRF2元数据经同源WebSocket进入已有双模型推理链路。浏览器能否解码须以选定文件
实际播放确认，不能仅凭MP4扩展名判断；页面不隐式转码或更换素材。

新页面与视频控制器复用 `LiveDetectionClient`、`FramePipeline.dispatch/drain`、Guidance及
原生语音/振动输出；原摄像头 `FramePipeline.run`、`camera.js` 和 `local-mvp.js` 不因视频
输入而改写。需要提醒时仍须用户启用Guidance和相应输出，并显式选择匹配语言的本地声音。

视频只按原生1倍速播放，用 `requestVideoFrameCallback` 观察新媒体PTS；相同PTS不再次
采集，30FPS仍为采集上限，12帧预算包含正在编码的帧，首帧独占。640、confidence0.25、
JPEG0.8、原生FP32、55秒会话/55秒首帧/10秒后续预算保持。低帧率源视频不会靠重复同一
画面凑出30个新帧；因上限、首帧等待或在途满额而未获准采集的源帧单列为未采集观察，
不是已经采集后丢弃的推理请求。冷加载期间视频继续1倍速前进，首帧后的源PTS跳跃应保留。

真正将画面draw到采集canvas时记录 `performance.now()`，作为PRF2的 `capturedAt`，由
原算法用于距离历史、相对速度与TTC；媒体PTS、presentedFrames与采集间隔另作观测。
不把媒体PTS写入算法时间，也不改写算法估速规则。自然1倍速播放时，实际采集间隔近似
源运动经过的时间；解码卡顿、浏览器调度或冷加载会使两者偏离。报告须保留媒体时间间隔、
采集时间间隔和二者漂移，不把这条回放链路当作带真值的速度、米制距离或TTC准确率验证。

文件结束先停止采集，排空已经获准采集的帧后结束会话；服务端55秒会话结束不再发送
尚未提交的编码工作，只完成已验证结果的显示。用户停止、页面隐藏、seek或播放速率变化
立即取消当前会话和自有输出。页面不循环、不自动续接或重启；再次运行须用户明确开始
新会话，重新建立tracker/history。断开后仍等待已提交GPU工作完成并释放槽位，不能用
界面停止时间证明GPU已被中断。输入时长超过55秒的文件不能因此绕过单次会话契约。

视频验收分别记录源视频FPS/独立PTS数、获准采集数、返回/解码交付数与FPS、未采集源帧、
采集→解码时延和全部慢帧。风险输出另外记录原源帧采集→真实语音引擎start或振动API接受
的时延、取消与过期，继续保留“≥10FPS且采集→告警<500ms”的批准目标；媒体帧率和解码
FPS均不能替代该告警指标。语音引擎事件仍不等于实际声响，振动API接受不等于马达实测。
连续视频和静态输入按各自范围验收，不混用采样方式或结果口径。

街景回放素材为MOT17官方数据集的07白天移动步行街、10夜间移动街景。
两个可选文件为 `backend/model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-browser-h264.mp4`
和 `backend/model_tests/datasets/street-video/mot17/MOT17-10-FRCNN-browser-h264.mp4`。
它们是显式转码的浏览器兼容副本；官方原始预览保留，帧数与30FPS不变，像素发生编码变化。
数据集选择、划分、标注、哈希和转换边界见 [视频数据说明](model_tests/DATASETS.md#连续街景视频用于本地联调)。
两个原始预览、两个H.264副本及来源清单属于版本管理范围；完整数据集、标注和逐帧
运行报告按目录规则单独管理。

启动两进程后，从 `backend` 运行以下独立Chrome工具。它使用原生本地文件选择与Blob解码，
分别明确执行手动停止和自然EOF两个会话，不启动服务、下载、转码或使用fake camera。
输出选择新的路径，保留每帧和失败结果；原生本地语音不可用时明确记录未验证，不替换声音。

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<已安装Playwright包的绝对路径>'
node ../frontend/tools/check_local_video_browser.mjs http://127.0.0.1:8766/integration/video/ .venv/tmp/local-mvp-video-next.json model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-browser-h264.mp4
```

验收方法见下一节；视频帧率、解码吞吐和采集到告警的时延分别衡量。

## 验收方法与边界

开发检查从 `backend` 执行，Web环境用于Django和协议测试，GPU环境用于实际双模型推理：

```powershell
# 读取专用本地key，不在终端输出密钥。
$env:DJANGO_SECRET_KEY = (Get-Content .env.local-mvp | Where-Object { $_.StartsWith('DJANGO_SECRET_KEY=') }).Substring(18)
$env:DJANGO_SETTINGS_MODULE = 'config.settings'
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\ruff.exe check .
node --test ../frontend/tests/*.test.mjs

# 静态功能检查；输出目录必须不存在。
.\.venv-inference\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --repeat 6 --output model_tests/outputs/NEW_STATIC_RUN
```

连续视频验收使用前一节的Chrome工具，选择浏览器可解码的文件，并为每次运行指定新的
JSON报告路径。工具分别检查用户停止与自然EOF；用于完整EOF检查的文件应短于55秒，
不能把服务端会话截止当作文件播放结束。页面不申请摄像头权限，也不自动转换输入素材。

| 项目 | 目标或检查方式 |
| --- | --- |
| 处理吞吐 | 至少10FPS；独立报告源帧率、实际采集帧数和解码交付帧数。 |
| 风险提醒时延 | 原源帧采集到风险输出开始小于500ms；语音引擎start与振动API接受分别记录。 |
| 生命周期 | 手动停止、EOF、隐藏页面和会话结束后清空自有请求、跟踪会话和在途工作。 |
| 本地边界 | 检测、深度、跟踪和规则风险由本机执行，检查浏览器和服务端的外部调用。 |
| 准确率 | 使用对应真值和固定评价协议；没有真值的回放只检查功能和输出关联。 |

性能目标不是运行结果声明。报告应保存首帧、全部后续帧、P95/最大时延、未采集源帧、
取消及失败，不过滤慢帧或把冷启动混入暖态吞吐。源PTS与实际采集跨度分别记录；
采集到解码、提醒提交、原生输出开始及实际声音/马达是不同测量边界。

风险提醒须关联原始session、frame index和capturedAt，新帧不能刷新旧提醒的计时。
静态重复图片、合成传输及输出替身不能代替真实运动、风险准确率或物理设备验证。
MOT17回放样本没有米制距离、速度、TTC或风险真值；真实手机、扬声器、马达和移动网络
需要单独验收。结构化运行报告留在本地，不作为仓库已提供的测试输入或性能保证。

## 迁云接口边界

迁云时保持浏览器同源→Web/中台→私网推理服务的协议、元数据和错误语义，替换部署地址与
运行配置；推理依赖/模型与Web依赖分别打包。推理worker限制回环连接；迁云前须明确
私网身份与TLS边界，不能仅改地址就把GPU端口开放公网。目标GPU重新创建模型/Graph或
自身编译缓存，不复制笔记本GPU二进制冒充云端可用工件。

现 `deploy_phone_live` 的full档位覆盖原前端、软件和算法；`deploy_space` 发布HF源码，
二者都不是本地中台的独立部署器；迁云需要独立发布配置，避免覆盖软件层和算法层的部署。
Web进程的资源限额不能直接用于GPU推理服务；CPU内存、VRAM和并发预算
应根据目标GPU真实验证明确设定。Windows本机不要求Docker，云端可分别容器化。
