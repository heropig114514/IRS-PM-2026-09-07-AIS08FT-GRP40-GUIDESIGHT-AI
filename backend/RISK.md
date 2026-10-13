# TTC 与可解释风险评分

链路：原检测 → BoT-SORT → 原米制深度/ROI 融合 → 原 TrackHistory 运动估计 →
`realtime.risk.enrich_object_risk()` → 标注/PRS1。新增阶段无状态，不增加第二套轨迹历史，
不改变 YOLO、深度、BoT-SORT、EMA、回归、置信度或 NMS。没有新增模型、依赖、TTS、导航。

## TTC

`estimate_ttc(smoothed_distance_m, relative_speed_mps, motion_state)` 返回秒或 `None`。
仅当距离为有限正数、速度有限且为负、状态为 `approaching`、接近速度
`abs(speed) >= min_closing_speed_mps` 时计算 `TTC = smoothed_distance_m / abs(speed)`。
默认最低接近速度0.20 m/s，包含等号。距离/速度的 None、NaN、Inf、布尔值、字符串、
非正距离以及其他运动状态均不产生有限 TTC；速度为0或正数也不产生 TTC。

超过默认10秒评估窗口的 TTC **保留原数值**，TTC 分项为0，理由包含
`ttc_beyond_horizon`；并不强制把其余距离/运动/方向/类别风险改为低。
当前帧原始深度无效、平滑深度缺失或 ID 未确认时输出 `ttc_sec: null`、
`risk_score: null`、`risk_level: unknown`、无告警。短暂缺深度仍保留原 TrackHistory
的运动估计，评分阶段不重置历史，也不把缓存距离当作当前测量。

## 规则及全部默认值

`RiskConfig` 在 `realtime/risk.py` 集中定义。配置只在启动/CLI 初始化时读取一次。
分数：`clamp(distance + motion + TTC + direction + class, 0, 100)`；每个数值只选一个
所属分段，不累计多个距离/TTC段。分数是规则权重之和，**不是事故概率**。
`RiskAssessment.components` 保留五个分项供 Python 调试；协议输出 `risk_reasons`，
避免每框再携带一个分项字典。相同输入/配置得到相同结果。

| 参数/分项 | 默认阈值与权重 |
|---|---|
| `min_closing_speed_mps` | 0.20 m/s，接近速度 ≥ 此值才计算 TTC/接近加分 |
| `max_ttc_sec` | 10.0秒，超过此值 TTC 分项为0 |
| `distance_bands` | (0,1.5]m→40；(1.5,3]→25；(3,5]→15；(5,10]→5；>10→0 |
| `ttc_bands` | (0,1.5]s→40；(1.5,3]→30；(3,5]→15；(5,8]→5；>8→0 |
| `motion_weights` | approaching→15；moving_away→−10；stationary→0；unknown→0 |
| `closing_speed_bands` | 接近速度 ≥1m/s额外+5；≥2m/s额外+10；仅取最高满足段 |
| `direction_weights` | front→10；front_left/front_right→7；left/right→3；behind/unknown→0 |
| `class_weights` | truck/bus→18；car/motorcycle→15；bicycle/scooter→12 |
| 同一类别表的中等权重 | person/animal/bird/cat/dog/horse/sheep/cow/elephant/bear/zebra/giraffe→5 |
| 同一类别表的低权重 | bench/chair/stop sign/fire hydrant/pole/sign→1 |
| `default_class_weight` | 未列入类别→5，类别本身不会单独触发告警 |
| `risk_level_thresholds` | medium≥25；high≥50；critical≥75；其余已知分数为low |
| `alert_priorities` | low/unknown→0；medium→2；high→3；critical→4；1保留为normal |
| `medium_alert_enabled` | true；medium仅在接近速度有效、平滑距离≤3m且为前方区域时告警 |
| `medium_alert_max_distance_m` | 3.0m，包含等号 |
| `medium_alert_directions` | front、front_left、front_right |
| `front_left_fraction` / `front_right_fraction` | 1/3、2/3；框中心在两界之间及边界上为front |

low/unknown 不告警，high/critical 告警；未触发告警的 medium 优先级也是0。
运动分类滞回期间标签与当前速度符号可能暂时不同：`approaching` 必须满足有效负速度
才获得接近加分；`moving_away` 必须满足有效正速度才减分。未知运动不会因运动单独加分，
但近距离仍可能高风险。提供的 TTC 若不符合当前公式或资格，不贡献 TTC 分项，并记录
`ttc_inconsistent`，避免不一致的输入制造紧急程度。

优先复用已有合法 `direction`；否则根据原始框中心的水平位置导出
front_left/front/front_right。框先夹到帧边界，非法几何输出unknown。
这个方向是**相对相机视野**，没有已知行走方向或路径交叉判断。框尺寸只用于方向，不用于测距。

## 配置及运行

可直接调用 `RiskConfig(...)`，或使用 JSON。下面是部分覆盖示例，并非自动应用的实验参数：

```json
{
  "min_closing_speed_mps": 0.20,
  "max_ttc_sec": 10.0,
  "distance_bands": [[1.5, 40], [3.0, 25], [5.0, 15], [10.0, 5]],
  "ttc_bands": [[1.5, 40], [3.0, 30], [5.0, 15], [8.0, 5]],
  "class_weights": {"bicycle": 12},
  "direction_weights": {"front": 10},
  "motion_weights": {"approaching": 15},
  "risk_level_thresholds": [25, 50, 75],
  "medium_alert_enabled": true
}
```

JSON 名称表（`class_weights/direction_weights/motion_weights/alert_priorities`）按键覆盖
默认值，未指定的键保留；分段数组、级别阈值及方向列表整表替换。错误字段/数值/排序/文件
明确报错，不自动修复、重试或继续用默认文件。TTC评分最大分段不能超过评估窗口。

从 `backend` 执行：

```powershell
# 本地规则/融合/协议自动化测试；需要按 README 配置 DJANGO_SECRET_KEY
.\.venv\Scripts\python.exe -X utf8 manage.py test model_tests.test_risk model_tests.test_tracking

# 实际双模型+跟踪+TTC/风险；输出必须是新目录
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --repeat 6 --output model_tests/outputs/risk-run-1

# 可选：自行保存上面的配置，或提供自己的显式参数
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --input model_tests/datasets/sequence-frames --risk-config model_tests/risk.json --output model_tests/outputs/risk-sequence-1
```

重复静态图只能测试字段/ID/静止，不会产生可信的接近 TTC。CLI 使用本机推理开始时间，
目录图像与原视频时间不同，不能据此评估真实视频速度/TTC。带时间戳的调用方可用
`detector.predict(jpeg, timestamp=source_seconds)` 提供同一序列递增源时间；此任务未更改采集协议。
实时原默认跟踪路径自动计算风险；独立照片测试默认关闭跟踪及风险。
CLI 把有效配置完整写入 `run.json.settings.risk`，字段保存在 `results.jsonl.metadata.boxes`。

HF 可设置 `PRS_RISK_CONFIG=models/risk.json`（路径相对 Space 根目录），不设置则使用默认。
自定义文件需自行提供，源码打包不上传用户配置或模型；这里只维护源码 allowlist，未发布。
HF根进程加载不可变配置，allocation内传给检测器；实际轨迹仍只属于现有 ObjectTracker。

## 输出示例

以下距离均指传给规则的有效平滑米数，方向均为front；实际输出还保留原框/类别/置信度等字段。

| 对象 | 距离/运动/速度 | TTC | 分数/级别 | 告警/优先级 |
|---|---|---|---|---|
| person | 20m / stationary / 0m/s | null | 15 / low | false / 0 |
| car | 5m / moving_away / +1m/s | null | 30 / medium | false / 0 |
| person | 0.8m / stationary / 0m/s | null | 55 / high | true / 3 |
| bicycle | 2m / approaching / −1m/s | 2s | 97 / critical | true / 4 |

```json
{
  "track_id": 7, "class_name": "bicycle", "confidence": 0.93,
  "bbox": [120, 80, 360, 620], "distance_m": 2.1,
  "smoothed_distance_m": 2.2, "relative_speed_mps": -1.2,
  "motion_state": "approaching", "direction": "front", "ttc_sec": 1.8333333333333335,
  "risk_score": 97, "risk_level": "critical", "alert_priority": 4, "alert_required": true,
  "risk_reasons": ["distance_m_le_3", "approaching", "closing_mps_ge_1", "ttc_s_le_3", "direction_front", "class_bicycle"]
}
```

高/紧急标签：`ID 7 | bicycle | 2.1m | approaching | TTC 1.8s | CRITICAL`。
近距静止高风险显示 `TTC N/A`。low/medium/unknown保留原普通标签，不新增面板或播报。
帧元数据新增 `risk_model: rules-v1` 和独立 `risk_ms`，后者不包括模型、跟踪、绘制及传输。
旧字段保持，前端继续接受没有风险字段的旧包；新版风险字段需完整成组，非法结果明确拒绝。
元数据仍256KiB、JPEG仍1MiB；完整300目标带风险理由的精度保留测试覆盖此边界。

## 检查与限制

测试覆盖 TTC有效/无效输入、最低速度、10秒窗口、距离/TTC/分数各边界、A–G风险场景、
速度加分、可选medium告警、配置错误、框方向、已有字段不变、真实TrackHistory融合、
真实BoT-SORT原predict集成、缺深度缓存策略、标注、300目标协议及worker/CLI配置复用。
本轮实际通过142项后端测试（含18项新增风险测试）、70项前端测试（含3项新增风险协议测试），
以及 `manage.py check`、`makemigrations --check --dry-run`、`ruff check .`、
`git diff --check`；迁移无变化，真实SDK跟踪测试没有跳过。Django检查用当前进程临时测试密钥，
未改配置文件。变更代码的注释、声明/变量索引已人工复核，未引入文档检查脚本。

实际本地RTX3060双模型运行 `model_tests/outputs/risk-smoke-20261010/` 完成6帧、每帧5目标，
ID全程稳定，原检测哈希/框及原始深度与 `outputs/bus-run-2` 完全一致。
最终5目标均stationary，TTC均null，风险27/33/30/27/27（medium），均不告警。
5目标 `risk_ms` 范围0.146至0.452ms，中位0.233ms；新阶段只处理已有CPU标量，
没有GPU推理、CPU/GPU数据搬运或模型加载。标注/编码/JSON传输开销未计入该计时。

同机纯CPU规则微测，每组1000次：100目标平均0.844ms/帧，300目标平均4.165ms/帧；
输入为有效接近状态的合成数据，无模型/跟踪/绘制。这些是本机测量，不代表HF、
手机端到端延迟或长期FPS。证据见
[local-validation.json](benchmarks/2026-10-10/risk/local-validation.json)。
真实手持视频、HF/PRS外部运行及距离/速度/TTC/风险真值准确率未验证；未提交或部署。

米制单目深度存在误差，深度输出沿相机轴，不是所有物体的欧氏径向距离。相机运动也会
造成距离变化；BoT-SORT的GMC仅补偿关联，没有把速度变成世界速度。TTC假设当前接近速度
恒定，代表外推到零深度的时间，不证明物体将与用户路径相交。EMA/历史回归/滞回存在延迟，
且当前实时使用处理开始时间而非硬件采集时间。风险规则尚未用真实带真值场景校准，
无有效输入的unknown不表示安全；此阶段不新增跨帧风险滞回、提醒节流、遮挡预测或TTS。

本轮新增：`realtime/risk.py`、`model_tests/test_risk.py`、`RISK.md`。
本轮修改：`realtime/detector.py`、`core/detector.py`、`zerogpu/live_worker.py`、`zerogpu/app.py`、
`tools/deploy_space.py`、`tools/deploy_phone_live.py`、`model_tests/run_depth.py`、
`model_tests/test_tracking.py`、`model_tests/README.md`、`TRACKING.md`、`README.md`、
`PHONE-LIVE.md`、`zerogpu/README.md`、`../frontend/live-detection-client.js`、
`../frontend/tests/live-detection-client.test.mjs`、`../frontend/README.md`。
新增验证证据：`benchmarks/2026-10-10/risk/local-validation.json`。
实际生成但Git忽略的文件：`model_tests/outputs/risk-smoke-20261010/run.json`、
`results.jsonl`、6张标注JPEG及旁边的 `risk-smoke-20261010-console.log`。
