# BoT-SORT 跟踪与相对距离变化

实时链路现在为：原 YOLO26n 检测 → BoT-SORT 关联 → 原 YOLO26 Depth → 原 ROI 中位数
融合 → 每 ID 距离历史/EMA/相对速度 → TTC/规则风险 → 标注及 PRS1 输出。
本文件说明跟踪/运动阶段；后接的无状态 TTC/风险阶段见 [RISK.md](RISK.md)。
距离仍来自米制深度输出，不使用框尺寸、焦距或跟踪器的 Kalman 位置估算距离。

## 插入位置与状态归属

项目检测使用已验证的自定义 CUDA Graph。因此接在 `decode_boxes()` 后调用 Ultralytics
`BOTSORT.update()`，没有替换成另一条 `model.track()` 推理路径。通过 SDK 返回的检测索引
把 ID 附回原检测框；保留全部检测，包括尚未确认 ID 的目标。跟踪器预测框不替换原框，
所以深度 ROI、置信度、类别和原检测输出保持原样。检测640/confidence0.25/JPEG80、
原 NMS/Graph/精度计划，以及深度768/FP32和 GPU ROI 融合均未调整。

`ResidentDetector.object_tracker` 在构造时创建一次；HF 的 `AllocatedDetector` 在每次
视频会话的 GPU allocation 内创建一次。它们各自拥有 `ObjectTracker.tracker` 和
`ObjectTracker.history`，不把可变状态放在共享模型、模块全局或逐帧函数中。
`TrackHistory.tracks[track_id]` 保存 `TrackState` 的有界 raw/time/EMA 队列和状态确认计数。
同一个实例串行处理同一个视频，ID 只在该会话及 `tracking_epoch` 内有意义。

默认实时推理启用跟踪。独立照片测试默认关闭；只有明确传 `--track` 才把全部输入及重复
当作一个有序视频序列。开始另一段视频应创建新的会话/实例，或在两段之间显式调用
`detector.object_tracker.reset()`；它同时清除 GMC、ID、距离历史，并增加 epoch。
逐帧 `predict()` 不构造或重置跟踪器。不要跨相机共享实例或把无关照片串成一个轨迹。

## BoT-SORT 与手机运动补偿

使用已固定的 Ultralytics8.4.171 包内官方 `botsort.yaml`：`tracker_type: botsort`、
`gmc_method: sparseOptFlow`、`with_reid: false`。其余关联参数使用该版本 YAML 原值；
没有选择 ByteTrack 或加载 ReID 模型。
[官方跟踪说明](https://docs.ultralytics.com/modes/track/)和
[该版本配置](https://github.com/ultralytics/ultralytics/blob/v8.4.171/ultralytics/cfg/trackers/botsort.yaml)
说明了 GMC 和 ReID 配置。

CPU 上的 OpenCV 稀疏光流估计相邻画面仿射变换，补偿跟踪关联的相机运动。首帧及缺少
有效特征/变换时 SDK 可能得到单位变换，不能保证所有手持抖动都被补偿。项目仅覆盖固定
版本的 GMC 调用钩子，使意外 GMC 异常显式进入不可用状态；升级 Ultralytics 时必须重新
检查此接口和 CMC 测试。新依赖 `lap==0.5.12` 已显式固定；先检查它再导入跟踪 SDK，
防止 SDK 在运行时自动安装依赖。

GMC 修正视觉关联，并不把深度速度变成目标在世界坐标中的速度。输出是目标相对手机的
距离变化：手机前进时静止目标也可显示 `approaching`。目前没有相机位姿估计或深度尺度校正。

## 配置与分类

数值默认值集中于 `realtime/tracking.py` 的 `TrackingConfig`：

| 参数 | 默认 | 含义 |
|---|---:|---|
| tracker | botsort.yaml | 固定官方 BoT-SORT 配置 |
| history_size | 8 | 每 ID 最多保存8个有效距离样本 |
| min_valid_samples | 3 | 少于3个样本输出 unknown、null 速度 |
| ema_alpha | 0.5 | 有效距离 EMA 权重 |
| stale_timeout_s | 2.0 | 清理不再检测到的目标历史 |
| motion_history_timeout_s | 2.0 | 距上一次有效深度超过此间隔，清空运动历史 |
| min_time_span_s | 0.1 | 估速所需最短有效样本时间跨度 |
| approaching_threshold_mps | 0.2 | 速度小于或等于负阈值为 approaching |
| moving_away_threshold_mps | 0.2 | 速度大于或等于正阈值为 moving_away |
| confirmation_updates | 2 | 切换分类需要连续2次候选状态一致 |

EMA：`s = alpha * distance + (1-alpha) * previous_s`，原 `distance_m` 不变。
使用窗口中所有有效样本的真实秒数和 EMA 距离做普通最小二乘回归 `s = a*t + b`，
`relative_speed_mps = a`。实现先减去起始时间，再计算中心化协方差/时间方差，避免
较大绝对时间戳损失精度；不再使用首尾两点差分。负数表示接近，正数表示远离，
两端阈值包含等号，内部为 `stationary`。

不足3个有效样本或时间跨度不足0.1秒时，速度 `null`、状态 `unknown`。
初次分类及后续状态改变均需要连续2次合格回归候选一致；默认第3个有效样本开始估速，
最早第4个有效样本确认分类。候选改变会重新计数，缺深度不计数也不修改已发布分类。
EMA 和滞回会带来延迟；固定每样本 EMA 权重的平滑时间尺度也受采样频率影响。

复用原 `TrackHistory`，新增实例 API，无额外状态管理器：

```python
from realtime.tracking import TrackHistory, TrackingConfig

history = TrackHistory(TrackingConfig())  # 视频开始时创建一次
for timestamp, distance in [(0.0, 5.0), (0.5, 4.5), (1.0, 4.0), (1.5, 3.5)]:
    estimate = history.update_motion_state(7, distance, timestamp)
# estimate: MotionEstimate(3.9375, -0.7125, "approaching")
```

`MotionEstimate` 是不可变标量快照，字段为平滑距离、相对速度、稳定状态。同一帧中不同 ID
可以使用相同时间戳，同一个 ID 的重复/倒退时间会报错。流水线 `TrackHistory.update()`
调用同一个 API，并传类别信息以处理类别变化；ID之间的队列完全独立。
单个有效更新只做最多8个点的简单运算（配置允许改变有界队列长度），无新依赖或 GPU 传输。

JSON 配置只需写想覆盖的字段，例如 `{"history_size": 10}`。HF 可设置
`PRS_TRACKING_CONFIG=models/tracking.json`，文件路径相对 Space 根目录，启动时只读取一次。
该文件需自行提供，源码发布不上传自定义配置或权重。CLI 使用 `--tracking-config <JSON路径>`，
同时必须启用 `--track`。未知字段、无效参数、非 BoT-SORT 配置会明确报错，参数不会自动修正。

默认时间戳是同一 worker 的单调 `perf_counter()` 逐帧推理开始时间；队列、处理耗时变化
会影响估速，当前输入协议不包含相机采集时间。调用 `predict(jpeg, timestamp=seconds)`
可在本地传入同一视频的真实递增帧时间；不能混用系统时钟或跨进程时间。
CLI 图像目录使用运行时间，不读取原视频 FPS，因此不能用它计算离线视频的真实速度准确率。

## 输出与异常

原 `class_id/label/score/xyxy/class_name/confidence/bbox/distance_m` 保留。新增对象字段：

```json
{
  "track_id": 7,
  "class_name": "person",
  "confidence": 0.93,
  "bbox": [120, 80, 360, 620],
  "distance_m": 3.5,
  "smoothed_distance_m": 3.9375,
  "relative_speed_mps": -0.7125,
  "motion_state": "approaching"
}
```

以上对应同一 ID 在0/0.5/1/1.5秒的距离5.0/4.5/4.0/3.5m。
JPEG 标签为 `ID 7 | person 0.93 | 3.5 m | approaching | -0.71 m/s`，
保留原始距离显示；`unknown` 不显示速度，即使第3个有效样本已经有待确认速度。

- 无 ID：ID/平滑距离/速度为 `null`，状态 `unknown`，使用原标注格式。
- `None/NaN/Inf/零/负值` 深度不进入历史，也不改变 EMA/回归/确认计数。
  短暂缺深度时返回尚未过期的平滑距离、速度及已发布状态，它们是历史估计。
  超过距最后有效深度的 `motion_history_timeout_s` 后，整个运动历史清空，速度/平滑距离
  为 `null`、状态 `unknown`；恢复深度后重新积累。持续有效时只按样本数保留最近8点，
  不把固定2秒当作整个样本窗口长度，因此较慢但间隔未超时的帧也可估速。
  有 ID 但没深度的标签仍为原格式。原深度模型加载/推理/地图契约失败依旧终止会话，不自动停用深度。
- 短时遮挡由 BoT-SORT 自身 lost-track buffer 处理；不输出没有检测的预测框。
  消失目标按 `stale_timeout_s` 清理，无检测帧也执行清理；有效深度间隔超时与此独立。
- 类别变更清除该 ID 的距离历史；新 ID 从空历史开始，不猜测与旧 ID 的距离连续性。
- 缺依赖/不兼容版本/错误 YAML 在初始化时报 `tracking_unavailable`。
  逐帧关联/GMC 意外异常或帧尺寸改变时，记录异常类型并锁定 `tracking_status: unavailable`，
  后续仍输出原检测及深度标签；直到显式重置/新会话才恢复，不重试、不替换跟踪算法。
- 非递增、负值或非有限帧时间报错，避免产生无限速度。

帧元数据增加 `tracking_model/status/epoch/gmc_method/with_reid/tracking_timestamp_s/tracking_ms`。
前端校验并保留四个对象字段，接受原始距离为 `null` 时尚未过期的历史估计，
但有运动深度字段时仍须有深度模型标识。显示服务端 JPEG，旧格式响应仍被接受。
完整300目标响应可能超过旧128KiB元数据上限，故 JSON 上限统一为256KiB，JPEG仍为1MiB，
总包上限1310728字节。发布须同时更新 HF、PRS 协议、前端及服务启动参数
`--ws-max-size 1310728`。本次只修改源码，尚未发布到现有公网服务。

## 本地运行与验证

从 `backend` 安装本地 GPU 依赖并运行；CUDA Torch 安装步骤见
[测试 README](model_tests/README.md)：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-depth.txt
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --repeat 6 --output model_tests/outputs/tracking-run-1

# 有序、同尺寸、连续的视频 RGB 帧；建议000001.jpg等零填充命名
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --input model_tests/datasets/sequence-frames --output model_tests/outputs/sequence-run-1

# Django配置仍需已有DJANGO_SECRET_KEY
.\.venv\Scripts\python.exe -X utf8 manage.py test model_tests.test_tracking model_tests.test_cli
```

以下为2026-10-10初版 BoT-SORT 的实际 GPU 验证记录（采用旧首尾差分/初次确认逻辑），
保留原始证据，不作为本轮回归逻辑的 GPU 性能结论：两个原预训练模型，公交图片重复6次，
5个目标全程 ID1至5稳定，第4帧起均为 `stationary`。首帧初始化使相邻推理时间超过历史
超时，历史重新积累，但没有重置跟踪器。输入哈希、原检测输出哈希、所有原框/类别/
置信度/原始距离与此前 `bus-run-2` 完全一致。检测 Graph 只在首帧初始化。
非首帧 `tracking_ms` 为10.46至29.22ms（中位14.77ms）；完整 predict 为107.98至201.17ms。
首帧7.38秒包括原 Graph/深度 predictor 初始化。额外工作是 CPU 图像转换/光流/关联及
少量标量历史；没有增加 GPU 模型或把稠密深度传到 CPU，仍只回传原来的距离向量。
这些是全部6帧的烟雾验证数据，不能作为持续视频 FPS 或与历史 HF 性能的对照。
结果在 `model_tests/outputs/tracking-smoke-20261010/`，汇总见
[local-gpu.json](benchmarks/2026-10-10/tracking/local-gpu.json)。

`model_tests.test_tracking` 含真实 SDK ID 连续性/遮挡/平移画面 GMC 测试，以及有界历史、
三种运动、样本不足、无效深度、过期清理、类别/ID变更、显式重置、异常锁定、标签、
300目标协议和 worker 生命周期检查。集成测试把真实 BoT-SORT 接入原 `predict()`，
使用受控检测输出及4.8/4.3/3.7/3.2m深度图，验证第4帧接近、第5帧无有效深度仍保留
运动估计及二进制协议往返。新增噪声、不规则时间/大时间戳、ID7与ID9隔离、
阈值等号、状态连续确认和短暂缺深度/有效间隔超时检查。
受控深度不证明真实测距准确率。未安装 GPU SDK 的纯 web 环境会跳过真实 SDK 测试；
本机已安装，相关测试实际执行。

初版 BoT-SORT 历史交付检查：119项后端测试、67项前端测试通过，真实 SDK 测试没有跳过；
`manage.py check`、`makemigrations --check --dry-run`、全量 `ruff check .`、`pip check`
及 `git diff --check` 通过。修改代码的注释、声明/变量索引已人工核对，并用 AST 辅助核对
Python 声明名称；项目没有 `tools/check_docs.py`，未声称通过完整自动文档校验。
标注 JPEG 已查看，框密集时沿用原布局，长标签可能相互遮挡。
本次未提交 Git，未部署外部服务。

手持实拍视频、HF allocation/公网前端部署、长时间性能、距离/速度真值及 ID切换率尚未验证。
ReID关闭时长遮挡、快速转动、相似目标交叉仍可能换 ID；单目深度噪声和框内背景会影响速度。
没有训练新模型，也没有改变数据划分、随机种子或评估指标。

## 本轮运动趋势改进与验证

2026-10-10，本轮124项后端测试和67项前端测试全部通过。其中26项跟踪/运动测试包括
请求的 A至I 场景及真实 BoT-SORT/CMC 测试，未跳过真实 SDK；集成测试使用受控深度图，
不证明物理速度准确率。Django check、迁移 dry-run、全量 Ruff、diff检查通过。
变更注释及声明/变量索引已人工核对，AST辅助确认 Python 名称及说明字段。
CPU标量检查为100个 ID连续1000次更新，共10万次调用，平均每批100个 ID约0.57ms，
各轨迹最终都只保留8个样本；此数字不包含检测、深度、跟踪关联或渲染，不是视频 FPS。
本轮未重新执行真实模型视频、手机或 HF 外部验证，未提交或部署。

本轮修改文件：`realtime/tracking.py`、`core/detector.py`、`model_tests/test_tracking.py`、
`frontend/live-detection-client.js`、`frontend/tests/live-detection-client.test.mjs`，以及
`TRACKING.md`、`model_tests/README.md`、`PHONE-LIVE.md`、`frontend/README.md`。
没有新增依赖，模型/BoT-SORT/深度实现及原始距离计算未修改。

## 初版 BoT-SORT 文件变更（历史）

| 类别 | 文件（除 frontend 外均相对 backend） |
|---|---|
| 跟踪及推理 | `realtime/tracking.py`（新增）、`realtime/detector.py`、`core/detector.py`、`zerogpu/live_worker.py`、`zerogpu/app.py` |
| 协议及前端 | `realtime/protocol.py`、`frontend/live-detection-client.js` |
| CLI及自动化测试 | `model_tests/run_depth.py`、`model_tests/test_tracking.py`（新增）、`model_tests/test_cli.py`、`core/test_depth.py`（说明更新）、`frontend/tests/live-detection-client.test.mjs` |
| 依赖及发布配置 | `requirements-depth.txt`、`zerogpu/requirements.txt`、`deploy/prs-camera.service`、`tools/deploy_phone_live.py`、`tools/deploy_space.py` |
| 说明 | `TRACKING.md`（新增）、`model_tests/README.md`、`README.md`、`MCP.md`、`DEPTH.md`、`PHONE-LIVE.md`、`zerogpu/README.md`、`frontend/README.md` |
| 验证证据 | `benchmarks/2026-10-10/tracking/local-gpu.json`（新增） |

实际生成但 Git-ignored：`model_tests/outputs/tracking-smoke-20261010/run.json`、
`results.jsonl` 和 `annotations/bus.jpg/0000.jpg` 至 `0005.jpg`。现有历史结果未被覆盖。
