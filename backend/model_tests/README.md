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
├── datasets/          # 存放自己的数据或公开数据集
└── outputs/           # 每次运行的 JSON、标注图与状态
```

## 直接运行（PowerShell）

从项目根目录进入本目录，使用独立GPU环境 `backend/.venv-inference`：

```powershell
cd backend/model_tests

# 默认 samples/bus.jpg；指定尚不存在的输出目录
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-run-3

# 放好自己的 RGB 照片后，处理整个目录
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1

# 显式重复同一图片，不丢弃首帧
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-repeat-1 --repeat 3

# 显式跟踪同一序列：重复图片可检查 ID 连续性和 stationary
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --track --repeat 6 --output outputs/tracking-run-1

# 自己视频的连续、同尺寸 RGB 帧，文件名按000001.jpg等排序
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --track --input datasets/sequence-frames --output outputs/sequence-run-1

..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --help
```

也可从 `backend` 使用模块入口：

```powershell
.\.venv-inference\Scripts\python.exe -X utf8 -m model_tests.run_depth --output model_tests/outputs/bus-run-3
```

两个入口相同。默认示例、模型和 manifest 路径按脚本位置定位；显式 `--input`、
`--output`、`--depth-weights` 等相对路径按当前终端目录解释。已有输出目录会报
`Output already exists; choose a new run directory`，需要换新目录名，不会覆盖旧结果。

## 数据集怎么放

例如：`datasets/my-photos/`、`datasets/coco/val2017/`、
`datasets/nyu-depth-png/images/val/`。可保留数据集原始目录、检测标签和深度真值。
`--input` 仅指定 RGB 图片或 RGB 子目录；不要指向同时包含深度/标签 PNG 的数据集根目录。
支持 jpg/jpeg/png/bmp/webp，递归读取全部匹配文件并按路径排序，不自动划分或抽样。
4个MOT17测试视频及 `datasets/street-video/SOURCES.json` 来源清单属于版本管理范围；
完整数据集、标注和生成输出单独准备，不随这些测试视频维护。
MP4用于独立的 `/integration/video/` 回放入口，当前图片脚本不直接读取视频文件。
公开下载入口与评估注意事项见 [DATASETS.md](DATASETS.md)。

## 模型与环境

图片推理使用独立GPU环境 `backend/.venv-inference`，共用
`backend/models/yolo26n.pt`、`backend/models/yolo26n.json` 和 `backend/models/yolo26n-depth.pt`。
Web与Django测试使用 `backend/.venv`；两个环境的依赖分别管理，不将Torch装入Web环境。
环境准备和固定依赖见 [本机MVP说明](../LOCAL-MVP.md#环境与模型准备)。

检测加载器验证既有YOLO26n权重和manifest，`--weights` 不是任意模型的通用接口。
兼容的深度权重通过 `--depth-weights` 显式指定。本脚本要求NVIDIA CUDA GPU，不自动下载
权重、切换CPU或单模型降级。BoT-SORT使用固定的 `lap==0.5.12` 依赖。

图片测试无需启动Django服务。深度融合、跟踪和风险配置分别见
[DEPTH.md](../DEPTH.md)、[TRACKING.md](../TRACKING.md) 和 [RISK.md](../RISK.md)。

独立照片默认不启用跟踪。`--track` 把全部输入/重复当作一个连续序列，不要用于无关的
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
输出还包含 `direction/ttc_sec/risk_score/risk_level/alert_priority/alert_required/risk_reasons`。
TTC 用平滑米数除以有效负速度的绝对值；本帧缺深度则 TTC为空、风险unknown，保留运动历史。
只有high/critical标签追加简洁TTC/等级，低风险保持原标注；`risk_ms`单独计时，不含推理。
首次完整 predict 耗时包含 Graph/predictor 初始化，不能直接作为稳态 FPS。

## 检查与结果边界

CLI和协议自动化检查从 `backend` 执行：

```powershell
.\.venv\Scripts\python.exe manage.py test model_tests
```

检查需要项目配置的 `DJANGO_SECRET_KEY`，完整开发检查见 [后端说明](../README.md#检查)。
运行生成的JSON、标注图和耗时保存在指定的 `outputs/<运行名>/` 下；仓库不附带本机运行
工件，已有输出目录不能覆盖，失败和取消也应保留在对应报告中。

单张图、静态重复和协议替身用于检查接口、ID生命周期及输出字段，不能证明真实视频的
速度、TTC、风险或米制距离准确率。正式比较需要对应真值、固定数据划分和评价协议，
具体方法见 [DATASETS.md](DATASETS.md)。
