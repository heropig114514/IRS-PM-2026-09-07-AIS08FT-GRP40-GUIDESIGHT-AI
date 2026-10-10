# 本地模型测试

运行入口、示例、数据集和输出统一放在本目录。脚本调用项目现有 YOLO26 检测/深度实现，
保持检测640、confidence0.25、JPEG80、FP32和深度768设置；两个模型各加载一次并复用。
当前导出预测和耗时，没有读取真值或计算准确率，也不会自动下载、训练、抽样、重试。

```text
backend/model_tests/
├── run_depth.py       # 单图/目录双模型测试入口
├── test_cli.py        # CLI 自动化检查，纳入 Django 测试发现
├── test_tracking.py   # BoT-SORT、历史/运动及流水线集成测试
├── README.md          # 运行及输出说明
├── DATASETS.md        # 公开数据集入口、数量建议、对比协议
├── samples/           # 随附 bus.jpg 和来源说明
├── datasets/          # 在这里添加自己的图片或公开数据集
└── outputs/           # 每次运行的 JSON、标注图；已迁入之前的结果
```

## 直接运行（PowerShell）

从项目根目录进入本目录，使用已有 `backend/.venv`：

```powershell
cd backend/model_tests

# 默认 samples/bus.jpg；bus-run-1、bus-run-2 已存在，所以用新目录
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-run-3

# 放好自己的 RGB 照片后，处理整个目录
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1

# 显式重复同一图片，不丢弃首帧
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-repeat-1 --repeat 3

# 显式跟踪同一序列：重复图片可检查 ID 连续性和 stationary
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --track --repeat 6 --output outputs/tracking-run-1

# 自己视频的连续、同尺寸 RGB 帧，文件名按000001.jpg等排序
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --track --input datasets/sequence-frames --output outputs/sequence-run-1

..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --help
```

也可从 `backend` 使用模块入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --output model_tests/outputs/bus-run-3
```

两个入口相同。默认示例、模型和 manifest 路径按脚本位置定位；显式 `--input`、
`--output`、`--depth-weights` 等相对路径按当前终端目录解释。已有输出目录会报
`Output already exists; choose a new run directory`，需要换新目录名，不会覆盖旧结果。

## 数据集怎么放

例如：`datasets/my-photos/`、`datasets/coco/val2017/`、
`datasets/nyu-depth-png/images/val/`。可保留数据集原始目录、检测标签和深度真值。
`--input` 仅指定 RGB 图片或 RGB 子目录；不要指向同时包含深度/标签 PNG 的数据集根目录。
支持 jpg/jpeg/png/bmp/webp，递归读取全部匹配文件并按路径排序，不自动划分或抽样。
数据内容和生成输出均 Git-ignored，目录中的说明文档保留在源码中。
公开下载入口与评估注意事项见 [DATASETS.md](DATASETS.md)。

## 模型与环境

共用 `backend/models/yolo26n.pt`、`backend/models/yolo26n.json`、
`backend/models/yolo26n-depth.pt` 和 `backend/.venv`。当前工作区已经准备好。
在本目录可通过 `--depth-weights ../models/yolo26s-depth.pt` 显式选择另一份已准备的兼容深度权重。
检测模型继续验证原有 YOLO26n manifest，不能用 `--weights` 任意切换检测架构。
本脚本需要可用的 NVIDIA CUDA GPU，现成预训练模型可直接推理。

新环境先从 `backend` 建立 `.venv`，再安装适合 GPU 的 Torch 与现有依赖。例如本项目当前 CUDA12.6 组合：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-depth.txt
```

BoT-SORT 新增固定依赖 `lap==0.5.12`，已纳入 `requirements-depth.txt`；原 GPU 包版本不变。
无需启动 Django 服务。融合实现、HF 配置和完整验证边界见 [DEPTH.md](../DEPTH.md)，
跟踪状态归属、运动参数及限制见 [TRACKING.md](../TRACKING.md)。
TTC/规则评分及全部阈值见 [RISK.md](../RISK.md)，无新增依赖。

独立照片默认不启用跟踪。`--track` 把本次全部输入/重复当作一个连续序列，不要用于无关的
COCO/NYU 单张图片或多个相机序列。原视频需先提供按时间排序的 RGB 帧，本工具不读取视频
或采集 FPS；速度使用本机推理开始时间，所以目录运行不能证明原视频真实运动速度。
可显式传 `--tracking-config <JSON路径>` 覆盖集中配置，必须同时启用 `--track`。
启用跟踪即同时运行 TTC/风险；`--risk-config <JSON路径>` 可显式覆盖集中规则，也需要
`--track`。完整有效规则保存在 `run.json.settings.risk`，不改动原运动参数。

## 输出怎么看

每个 `outputs/<运行名>/` 包含 `run.json`（状态、设置和哈希）、`results.jsonl`
（每张图/每次重复的预测）以及 `annotations/<图片名>/<重复编号>.jpg`。
看标注图，再看 `metadata.boxes` 的 `class_name/confidence/bbox/distance_m`；距离单位为米，
无有效距离是 `null`。框坐标对应缩放后的帧；文件记录原图与帧尺寸，便于后续对齐真值。
启用跟踪时还可查看 `track_id/smoothed_distance_m/relative_speed_mps/motion_state`。
速度单位 m/s，负数接近，正数远离。EMA后按所有历史时间戳线性回归，至少3个有效样本
开始估速，连续2次候选一致才分类，默认最早第4个有效样本确认；标签包含已确认速度。
短暂缺距离保留历史估计，超过 `motion_history_timeout_s`（默认2秒）才清空为
`unknown`/`null`；当前原始 `distance_m` 仍为 `null`。
新增 `direction/ttc_sec/risk_score/risk_level/alert_priority/alert_required/risk_reasons`。
TTC 用平滑米数除以有效负速度的绝对值；本帧缺深度则 TTC为空、风险unknown，保留运动历史。
只有high/critical标签追加简洁TTC/等级，低风险保持原标注；`risk_ms`单独计时，不含推理。
首次完整 predict 耗时包含 Graph/predictor 初始化，不能直接作为稳态 FPS。

之前的 `test-results/depth/` 文件已迁至 `outputs/`，包括你的
[bus-run-2/run.json](outputs/bus-run-2/run.json)。历史 JSON 中的输入绝对路径和参数
保持原始记录，不因搬家改写；新运行会记录 `samples/` 的新路径。原运行文件内容已按 SHA256 核对。

CLI 自动化检查从 `backend` 执行 `python manage.py test model_tests`，仍需项目配置的
`DJANGO_SECRET_KEY`。项目完整检查按 [后端 README](../README.md) 执行。

2026-10-10 整理后验证：直接脚本及模块入口的 `--help` 正常；在本目录实际运行双模型，
`outputs/layout-verification-20261010/` 完成 1 帧/5 个目标。全量97项后端测试、Django check、
迁移 dry-run、Ruff 和 `git diff --check` 通过；变更代码注释及声明/变量索引已人工复核。
实际距离准确率、真实手机和 HF 外部部署未验证。本次未提交或部署。

同日初版跟踪 GPU 验证（旧估速逻辑）在 `outputs/tracking-smoke-20261010/`：6帧保留5个 ID，
有效历史积累后显示 `stationary`；原检测哈希、所有原框及原始距离与 `bus-run-2` 一致。
这是本地静态重复验证，真实视频跟踪及速度准确率仍需时序数据和真值。

随后线性回归/缺深度保留改进通过全量124项后端、67项前端及 Django/迁移/Ruff/diff检查。
运动/跟踪模块26项检查包含噪声、不规则时间、ID隔离、滞回及有效深度间隔超时。

随后TTC/规则风险通过142项后端、70项前端及Django/迁移/Ruff/diff检查，包含18项新增风险
测试和真实BoT-SORT原链路集成；无新增依赖。双模型实际6帧结果在
[risk-smoke-20261010/run.json](outputs/risk-smoke-20261010/run.json)，5个ID稳定，
原检测哈希/框/原始深度与bus-run-2一致，最终stationary/TTC为空且不告警。
规则阶段中位约0.233ms（5目标）；完整配置/评分/边界和限制见 [RISK.md](../RISK.md)。
静态重复图片不验证接近速度或真实TTC；真实手机/HF/带真值风险准确率仍未验证，未提交或部署。
