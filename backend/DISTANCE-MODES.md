# 可切换目标测距

`distance_mode` 默认是 `depth`，保留原 YOLO26 检测、YOLO26-Depth 和 ROI 中位数算法。
新增模式复用原检测、BoT-SORT、运动估计、Risk Assessment、标注和二进制接口。
Web/中台负责转发，测距模式由 GPU 进程启动配置选择；切换后重启推理进程/会话。

## 数据流与输出

| 模式 | 距离阶段 | Depth 权重/调用 | 下游 |
|---|---|---|---|
| A `depth` | 每帧原图 bbox 内正有限深度的中位数 | 加载一次，每帧调用 | 原跟踪运动、TTC、风险、Guidance |
| B `geometry` | `fy_px * object_height_m / bbox_height_px` | 不读取、不加载、不激活、不调用 | 使用相同距离字段 |
| C `temporal_depth` | Depth 帧调用 A 的融合；其余帧按 Track ID 取 Last Valid Distance | 加载一次，首帧及每隔 N 帧调用 | 使用相同距离字段；缓存值不计为新运动测量 |

共同链路：JPEG → 原检测/反 letterbox → BoT-SORT 关联 → 所选距离策略 →
原运动历史 → 原 TTC/风险 → 标注 JPEG 和对象元数据。CLI 独立照片可以关闭跟踪；C 必须开启。
检测模型、检测精度、输入640、confidence0.25、JPEG80及深度模型768/FP32均沿用原配置。

对象沿用 `class_id/label/score/xyxy`，测距输出沿用
`class_name/confidence/bbox/distance_m`，跟踪时仍有 `track_id` 等运动/风险字段。
单位为米，缺失距离为 JSON `null`，不新增替代性的 `distance` 字段。
B/C 另加 `distance_source`：`geometry`、`depth`、`cached` 或 `unavailable`。
A 的对象字段不变。B 的帧元数据使用 `distance_estimator: pinhole-height-v1`，不声明 `depth_model`。
浏览器仅增加所选策略的标识校验；Guidance、语音/振动调度及风险规则无需修改。

## 配置

集中默认值在 [distance_config.py](realtime/distance_config.py)，可编辑的完整 JSON 示例在
[models/distance-config.json](models/distance-config.json)。文件默认示例仍选择 A：

```json
{
  "distance_mode": "depth",
  "depth_interval": 3,
  "focal_length_px": 700.0,
  "reference_width_px": 640,
  "object_heights_m": {
    "person": 1.7, "bicycle": 1.1, "motorcycle": 1.2,
    "car": 1.5, "bus": 3.2, "truck": 3.0
  }
}
```

配置文件需要通过 `--distance-config` 或 `PRS_DISTANCE_CONFIG` 显式选择。
优先级：集中默认值 < JSON 文件 < 环境变量 < CLI 参数。
`PRS_DISTANCE_CONFIG` 的相对路径按 backend 根目录解析；CLI 显式文件路径按当前终端目录解析。
`PRS_DISTANCE_MODE` 覆盖模式；`PRS_DEPTH_INTERVAL` 覆盖 C 的间隔。
无效模式、非正整数间隔、无效焦距/高度和未知 JSON 参数直接报错。

`focal_length_px` 是参考图像宽度 `reference_width_px` 下的**垂直焦距 fy**，不是毫米焦距。
因为 bbox 属于当前解码画面而非检测器的 letterbox 方图，实际使用
`fy_current = focal_length_px * frame_width / reference_width_px`。
真实标定的内参 `K[1,1]` 可以直接填入该配置，并把参考宽度设为标定图像宽度。
缩放假设画面保持宽高比且没有裁剪；有裁剪/旋转的摄像头应使用对应输出画面的标定参数。
默认700px及类别高度只是实验假设；姿态、车辆型号、遮挡和截断会影响 B 的距离。
不在高度表中的类别，以及无效、空、越界 bbox 返回 `null`；不会猜测其他类别高度。
高度表按整个字典替换，方便显式限制参与实验的类别。

## 分别运行 A/B/C

从 `backend` 运行，`$gpuPython` 选择已安装固定 CUDA 推理依赖的解释器。
现有分离式部署通常为 `.venv-inference`；组合开发环境可使用已有 `.venv`。
不需要为本功能安装新依赖。输出目录必须尚不存在。

```powershell
$gpuPython = '.\.venv-inference\Scripts\python.exe'

# A：原方案；不传 --distance-mode 时默认也是 A
& $gpuPython -X utf8 -m model_tests.run_depth --distance-mode depth --track --repeat 9 --output model_tests/outputs/abc-a

# B：显式配置焦距/类别高度；不需要 Depth 权重
& $gpuPython -X utf8 -m model_tests.run_depth --distance-config models/distance-config.json --distance-mode geometry --track --repeat 9 --output model_tests/outputs/abc-b

# C：首帧、第四帧、第七帧……调用 Depth
& $gpuPython -X utf8 -m model_tests.run_depth --distance-mode temporal_depth --depth-interval 3 --track --repeat 9 --output model_tests/outputs/abc-c

# C 每帧 Depth，与 A 使用相同测量/融合及下游算法
& $gpuPython -X utf8 -m model_tests.run_depth --distance-mode temporal_depth --depth-interval 1 --track --repeat 9 --output model_tests/outputs/abc-c1
```

`depth_interval=2/3/5` 分别隔2/3/5帧推理；只对 C 生效，A 始终每帧 Depth。
`--input <连续同尺寸RGB帧目录>` 可替换随附样例；排序及 CLI 时钟沿用原实现。
不同照片/相机应分别运行；静态重复仅用于检查接口，不证明视频运动精度。

本地浏览器实时/视频入口也使用同一配置。启动前在 PowerShell 设置，例如：

```powershell
$env:PRS_DISTANCE_CONFIG = 'models/distance-config.json'
$env:PRS_DISTANCE_MODE = 'geometry'       # A: depth；C: temporal_depth
$env:PRS_DEPTH_INTERVAL = '3'             # C 可改为 1、2、3、5
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp
```

按原 [LOCAL-MVP.md](LOCAL-MVP.md) 准备独立环境后，打开 `/live/` 或 `/integration/video/`。
启动器继承这些进程环境变量；B 的预检只要求检测权重；A/C 仍要求两份权重。
单独启动 `integration.inference.asgi:application` 也读取同样环境变量。
推理 worker 不读取 Django `.env`，请设置进程变量或显式传配置文件。
模式在整个 worker 生命周期固定，避免会话中途混用距离、运动缓存和风险来源。

HF Space 使用同名环境变量；A/C 继续需要原 `PRS_DEPTH_WEIGHTS`。
B 即使保留该变量也不会构造 DepthEstimator。HF 未配置深度权重且未选 B/C 时，
仍保留旧的 detection-only 行为；C 缺少深度权重会启动失败。
源码 staging allowlist 已包含新增模块和 JSON；此功能不自动部署或设置远端变量。

## C 的时序缓存

`TemporalDepthCache` 持有 `track_id -> TemporalDepthEntry`，字段包括
`last_distance/last_depth_frame/timestamp/class_name`，不保留稠密 Depth Map。
只写入 Depth 帧的正有限距离；非 Depth 帧只读取最近一次有效值。
Depth 帧当前无效时输出 `null`，保留先前有效记录供后续非 Depth 帧使用。
新目标/没有 ID/还没有测量的目标输出 `null`，等待下一 Depth 帧。

当前帧中消失的 ID 立即移除，重新出现也要等待新测量；类别改变会清除对应项。
显式 tracker reset 改变 epoch，新会话或画面尺寸变化清空全部缓存并重新以 Depth 帧开始。
tracker 失败输出空 ID，缓存随之清除。缓存只与当前可见目标数量有关。
性能统计也在 tracker epoch 改变时重新计数。

缓存值用 `distance_source=cached` 标识。运动模块依然接收原有对象接口，但不会把缓存值
追加到距离/时间样本队列；避免在静止复制值之间生成虚假的速度测量。
原运动历史超时机制继续生效：有效 Depth 间隔超过默认2秒时可能回到 unknown，直到积累
足够的新测量。风险按原规则消费当前距离及已有运动估计；C 的缓存会带来响应滞后。
插值、速度预测和 Kalman Filter 尚未实现，可在缓存/策略接口内扩展。

## 性能记录和实验边界

保留原 `inference_ms/forward_event_ms/depth_ms/tracking_ms/risk_ms/server_ms` 的含义。
新增帧字段：

| 字段 | 范围 |
|---|---|
| `detection_ms` | JPEG 解码、预处理、检测、bbox 解码，含首帧 Graph 设置 |
| `distance_ms` | A 的深度推理/融合；B 的几何计算；C 的深度/缓存阶段 |
| `end_to_end_ms` | worker 从解码到完成标注，与 `server_ms` 相同 |
| `fps` | `1000 / end_to_end_ms`，单帧处理速率 |
| `depth_frame` | 当前是否实际调用 Depth 模型 |
| `depth_interval` | 实际策略间隔：A=1，B=0，C=所配置 N |

`performance` 提供 `total_frames/depth_model_calls/actual_depth_inference_ratio`、
`average_detection_ms/average_distance_ms/average_end_to_end_ms/fps`，以及
`depth_frame_count/non_depth_frame_count/average_depth_frame_ms/average_non_depth_frame_ms`。
累计 FPS 是 `1000 * total_frames / sum(end_to_end_ms)`，不是逐帧 FPS 的平均值。
空分组的平均耗时为 `null`。C 的首帧必做 Depth，因此有限长度序列调用率为
`ceil(frames/N)/frames`，长序列趋近 `1/N`。

统计仅用常数大小的计数与求和，不增加逐帧日志或 I/O，不剔除冷启动帧。
沿用已有 GPU 同步边界：检测 runner 返回 CPU 结果，深度融合返回距离标量后计时。
这里的 end-to-end 是服务端流水线时间，不包括网络、输入节奏、模型加载、校验元数据、
磁盘保存或浏览器显示。实际浏览器耗时/送达 FPS 继续使用现有 frame-pipeline 统计。

CLI 每帧保存在 `results.jsonl.metadata`，最终累计摘要保存在 `run.json.performance`；
有效策略/相机参数和模型哈希也写入 `run.json`。B 的深度哈希和路径为 `null`。
比较精度需要独立的距离真值；单图 smoke test 和调用次数不能证明物理准确率或持续视频 FPS。

## 实现位置与检查

`realtime/distance.py` 封装几何、TemporalDepthCache 与策略调度；
`realtime/distance_config.py` 管理配置；`realtime/performance.py` 管理统计。
`ResidentDetector` 只将原深度阶段委托给策略；`realtime/depth.py` 原算法不变。
本地/ZeroGPU 构造器及 CLI 按模式选择是否加载 Depth 模型。
本地握手/浏览器校验增加显式 B/C 分支，仍要求原检测身份、跟踪、风险及采集时钟。

从 backend 运行 `python manage.py check`、
`python manage.py makemigrations --check --dry-run`、`python manage.py test`、`ruff check .`；
前端运行 `node --test tests/*.test.mjs`。
`core.test_distance_modes` 检查公式/缩放/缺失值、A 融合兼容、C 调用间隔1/2/3/5、
新旧 ID 隔离/epoch/几何重置、缓存与运动样本隔离、B 不加载 Depth、协议和统计。
真实 GPU、手机、HF 与真值实验需要分别验证，单元测试不替代这些验证。
