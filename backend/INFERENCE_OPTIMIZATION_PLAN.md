# 服务推理优化计划与执行记录

更新日期：2026-10-20。代码起点：`main` / `5c70f3b`。

目标是建立当前版本的可复现性能基线，再依据测量结果优化深度推理、启动与传输，最后评估会话并发。分别记录纯模型计算、完整帧处理和浏览器端到端延迟，不将不同硬件或功能组合的历史数据混作当前基线。

## 范围与实验约束

- 用户已明确部署目标：本机或自有服务器的常驻 GPU 服务。后续实时链路为客户端 → ASGI → 常驻 CUDA 模型 → 检测、跟踪、可选深度、运动与风险 → JPEG 返回。
- 现有 HF/PRS 路径和实验记录保留；新增显式常驻入口，不依赖 HF 凭据，不把历史 HF 配置隐式套用到本机。
- 场景描述：PRS → DashScope Qwen-VL，单独评估连接与响应耗时。
- 保留当前部署计划、权重身份、检测 640 / batch 1 / confidence 0.25 / JPEG80、深度 768、现有评估指标和数据划分。
- 实验候选单独记录参数与质量结果；不得隐式替换线上配置、添加自动重试或降级，不能通过隐藏慢帧、丢弃请求来宣称加速。
- 当前授权用于文档、环境准备、本地测试和可验证的代码优化。远程部署、付费资源、修改线上容量及权重发布不属于本轮默认操作。
- 首帧与稳态分开报告；所有输入、返回、失败和未完成帧均计数。跨机器仅比较各自时钟上的持续时间，嵌套指标不得相加。

## 执行顺序

| 步骤 | 工作与产出 | 完成条件 | 状态 |
| --- | --- | --- | --- |
| 1 | 核对硬件、Python、依赖和权重；运行现有检查；建立检测、检测加深度、完整跟踪风险的基线 | 记录环境、模型与输入身份、首帧、稳态分布和适用范围 | 本机基线完成 |
| 2 | 分析深度阶段并选择独立优化实验 | 配对测量及数值一致性检查，满足质量要求后才纳入服务 | 静态性能及多输入一致性完成；真实视频和真值由同事提供，候选仍为独立实验 |
| 3 | 新增常驻 GPU 服务入口，消除每会话模型重载及本地 HTTP 交接 | 显式启动、模型复用、二进制交付、启动失败可诊断 | 已完成，见 RESIDENT.md；首帧初始化仍单列 |
| 4 | 验证会话隔离、背压、取消和本机传输 | 单 GPU 串行执行、有限在途帧、断开后不提前复用模型；真实 GPU 重连验证 | 已完成单机验证与 60 秒长测；24 FPS 完整流水线仍有排队限制 |

## 基线协议

先复用仓库随附的 `model_tests/samples/bus.jpg` 和现有显式运行配置进行功能与计时验证。静态样例仅证明对应输入下的行为和耗时，不证明真实视频 FPS、测距准确率或风险准确率。稳态分位数需要明确记录样本数；样本不足时不宣称可靠尾延迟。

测量分为三层：

1. CUDA 计算：`forward_event_ms`；另列包含搬运的 `inference_ms`。
2. 帧处理：预处理、检测后处理、跟踪、深度及融合、风险、绘制编码、完整调用耗时。
3. 服务交付：PRS/HF 队列和交接、客户端采集到结果/解码、首帧、帧交付数、稳态吞吐。

复用 `model_tests/run_depth.py`、`tools/benchmark_zerogpu_live.py` 和前端浏览器基准。现有本地双模型入口采用 FP32，对比时必须保留该身份；它不是部署计划中 FP16 编译方案的等价复现。先核对已有计时定义，避免把包含框解析或同步的字段误称为纯 forward。

## 质量与验证

- 检测精度候选沿用部署计划的 COCO 准入：同条件至少三次完整 5000 图评估，AP 降幅不超过 0.5 个百分点。短样例一致性不能代替此门槛。
- 深度候选需要独立的深度及距离质量评估；检测 AP 合格不代表深度合格。
- 修改代码时同步维护文件头、声明索引及函数/类说明。
- 在 `backend/` 执行 `python manage.py check`、`python manage.py makemigrations --check --dry-run`、`python manage.py test`、`ruff check .`；按改动范围补充前端测试和真实模型验证。
- 不将 mock 测试通过写成远程 HF、DashScope 或真实手机验证通过。

## 执行记录

### 2026-10-20 推送与下一步执行计划

用户已明确授权将当前成果推送到原 GitHub 仓库，并继续下一步工作。本次按以下顺序执行：

1. 获取远端状态，复核待提交文件，重跑后端规定检查和前端测试；将常驻服务、页面接入、独立实验工具及完整证据一起提交并推送。历史记录中的“未提交／未推送”保留其当时含义，后续状态在本节追加。
2. 对现有深度 Graph 完整流水线做稳态调用剖析，排除首帧模型初始化和 Graph 捕获；保留原公交输入、31帧协议、FP32、图像尺寸、跟踪参数及全部慢帧。
3. 在剖析前后各运行同配置无剖析对照，单列剖析扰动；结合实际 SDK 源码拆分深度预处理、同步、后处理、跟踪及 JPEG 开销。剖析计时不冒充浏览器成绩或纯 GPU kernel 时间。
4. 根据证据确定下一个独立候选，同步记录检查与限制。默认服务继续使用原实现；真实视频和深度真值仍待同事提供。

### 计划建立

- 工作区起点干净，尚未修改实现或创建提交。
- 本轮结果和下一步条件持续写入本文；测试日志与机器可读记录置于 `benchmarks/2026-10-13/`。
- 历史文档引用的部分 Notebook、原始实验目录和模型权重不在当前克隆中，步骤 1 将核对可复现范围。

### 环境与基线准备

- 本机 GPU：NVIDIA GeForce RTX 3060 Laptop GPU，6 GiB，驱动 610.74；与 HF Blackwell 环境分开记录。
- 已创建项目独立 `backend/.venv`，Python 3.12.14；开发直接依赖按仓库锁定版本安装。
- 官方检测与深度权重已下载至 Git-ignored 的 `models/`，两者 SHA256 均与仓库既有身份记录一致。
- 前端 80 项测试通过；最终后端 160 项测试全部通过，无跳过；Django check、迁移检查、pip check、全仓库 Ruff 通过。
- 新增 `tools/benchmark_inference.py`、`tools/depth_graph_probe.py`，以及统计/路径边界/真实 CUDA 缓冲所有权四项测试；未启用实验参数时维持原 FP32 基线。
- Torch 2.8.0+cu126 / torchvision 0.23.0+cu126 已安装，与历史本地 GPU 记录一致。完整依赖快照见 [environment.txt](benchmarks/2026-10-13/environment.txt)。

### 本轮探测命令

每种模式使用独立 Python 进程，显式执行 31 帧：首帧单列，后续 30 帧用于初步分布。全部使用随附公交图，不改变原有质量数据集或生产配置。这是新建的静态诊断协议，不用于证明尾延迟保证或准确率。

从 `backend/` 执行：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode detection --frames 31 --output benchmarks/2026-10-13/detection
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode depth --frames 31 --output benchmarks/2026-10-13/depth
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/2026-10-13/full
```

每个目录保留 `report.json` 和 `frames.jsonl`，记录输入/权重身份、全部结果、首帧、P50/P95/P99、显存峰值。输出目录已存在时拒绝覆盖；失败不自动重试。`depth_ms` 当前包含检测框解析与深度融合，不能当成深度模型纯 forward。

### 步骤 1 本机基线结果

| 工作负载 | 首次 predict | 后续 30 帧 P50 | 后续 30 帧 P95 | Torch 峰值 allocated 显存 |
| --- | --- | --- | --- | --- |
| 仅检测 | 704.01 ms | 12.53 ms | 13.51 ms | 95.61 MiB |
| 检测加深度 | 3870.62 ms | 36.42 ms | 47.04 ms | 211.73 MiB |
| 检测加深度跟踪风险 | 3296.54 ms | 42.36 ms | 50.30 ms | 211.73 MiB |

上述首帧不含单列的模型构造时间，显存为 PyTorch allocated 峰值，非进程或整卡占用。采用本机 FP32 控制路径，不能与 HF 的 FP16/编译配置直接比较。各进程初始化与全部计时保存在原始报告中。

深度 SDK 的已有同步计时显示，在完整链路中，深度预处理 P50 2.11 ms、推理 P50 14.53 ms、后处理 P50 0.16 ms；跟踪阶段 P50 6.56 ms。优先实验深度 forward 的调度开销，有现有测量依据。

### 步骤 2 深度 Graph 独立对照

实验工具仅在 `--depth-graph` 明确指定时替换本地 SDK 的 inference callable，保持 FP32、权重、预处理、后处理和 ROI 统计。首帧使用原实现完成初始化，然后单独预热/capture；每次输出 clone，避免下一帧覆盖上一帧。形状、类型、设备或调用选项变化会明确失败。

各组均使用独立进程、同一图片、31 帧，按原配置 → Graph → 原配置顺序执行：

| 工作负载 | 前置对照 P50 | Graph P50 | 后置对照 P50 | 相对前置对照的整帧时延下降 |
| --- | --- | --- | --- | --- |
| 检测加深度 | 37.01 ms | 30.22 ms | 37.76 ms | 18.35% |
| 完整链路 | 43.28 ms | 34.58 ms | 43.20 ms | 20.12% |

完整链路中的深度 SDK 推理 P50 从 14.87 ms 降至 7.25 ms。两组 Graph 准备额外耗时约 250/246 ms，必须计入启动成本；首帧字段自身不代表实验方案的完整就绪时间。

准备输入上的全部深度原始张量通过 `rtol=0, atol=0` 比较。检测加深度三次运行共 93 帧的检测原始哈希、框/距离及标注 JPEG 全部一致。完整链路三次运行按帧序比较，检测哈希、目标身份/原始距离、标注 JPEG 和运动状态也均一致。时间相关元数据未要求字节相同。

此候选只验证了静态公交图片，以及单元测试中的不同像素输入和输出所有权；没有深度真值指标、不同画幅/移动视频测试或 HF 分配验证。**候选尚未接入生产 `DepthEstimator`，不得把约 20% 的本机稳态收益写成线上收益。**

复现示例，需使用新的输出目录：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-graph --output benchmarks/NEW_RUN/full-graph
```

### 步骤 3 启动与本地交接

已补齐官方 COCO8 原四张验证图，保持既有传输基准样例集合和 ≥10FPS/最大500ms 门槛。数据集归档及图片 SHA 见 [transport-fixtures.json](benchmarks/2026-10-13/transport-fixtures.json)。从 backend 执行：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_zerogpu_live --local-native --origin http://127.0.0.1 --fps 12 24 --seconds 10 --output benchmarks/NEW_RUN/transport-local.json
```

| 本地输入 | 稳态交付 | 实际交付 FPS | P95 延迟 | 最大延迟 | 原门槛 |
| --- | --- | --- | --- | --- | --- |
| 12 FPS，10 秒 | 120/120 | 12.00 | 53.23 ms | 67.87 ms | 通过 |
| 24 FPS，10 秒 | 240/240 | 22.74 | 538.47 ms | 638.48 ms | 未通过 |

另有 4 帧初始校验，worker 总计确认 364 帧，全数交付，稳态没有重复 Graph 初始化。24FPS 的 HF 父队列 P50 165.98 ms、最大594.68 ms，同时 GPU forward 存在长尾；当前证据不能把全部延迟归因于 TCP。原失败报告与日志均保留，没有降采样、删慢帧或修改通过标准。

该现有基准将不同尺寸的独立图片依次送入默认跟踪器，出现明确的 BoT-SORT unavailable 状态。因此它只用于本地检测/交接诊断，不能证明连续视频跟踪能力。HF 的 GPU 分配、JIT 缓存恢复、公网往返及真实手机采集/显示仍未验证。

发现并修复 `zerogpu/runtime.py` 的纯耗时统计：本机 Python3.12 的 `monotonic` 使用15.625ms分辨率的 GetTickCount64，导致31帧检测中21帧的 `inference_ms` 被记录为0。改用同样单调的 `perf_counter` 后，同条件31帧无0值，原始预测/JPEG保持一致。只改统计时钟，没有改变截止时间、调度、模型或数值配置。证据见 [clock-validation.json](benchmarks/2026-10-13/clock-validation.json)。修复前报告的 `inference_ms` 不宜用于短阶段比较；本次主要结论使用原本已是高精度的 `predict_wall_ms` 和 SDK 计时。

### 步骤 4 容量与部署边界

- PRS 的 `HFLiveRelay` 使用一个进程内视频槽位；HF `LiveTransport` 也只接受一个活动视频会话。12帧窗口限制单会话在途工作，并不表示支持12个用户。
- 跟踪状态和 Graph/缓冲必须按会话与 GPU 分配隔离；增加 Uvicorn worker 会使进程内限流分裂，不能直接当成现有单会话服务的扩容措施。
- 现有测试已验证忙碌拒绝、顺序/credit 边界、失败和最终帧交付；不把这些契约测试称为真实多用户压测。
- VLM 仍是 DashScope API，每分钟10次、最多2个并发，每次新建HTTPS连接并等待完整响应。缺少访问配置，未进行外部调用、连接池或流式输出实验。
- 用户已选择本机或自有服务器常驻 GPU。常驻服务首版采用单进程、单活动视频会话和串行 GPU 执行；12 帧是背压窗口，不是 12 用户。不同会话重建跟踪状态，共享模型及检测 Graph。缺少 HF 凭据不阻塞这一目标。

## 后续执行条件

### 2026-10-20 深度 Graph 服务实验执行顺序

1. **已完成**独立测试 ASGI 入口，复用原串行服务、会话隔离和二进制协议；默认服务不变。首帧准备成本计入响应，重连复用固定形状 Graph；形状变化明确失败，不自动回退或重新捕获。
2. **已完成**原实现／Graph／原实现三组实际浏览器测试，各两次181帧及停止／忙碌检查；30FPS、12窗口、FP32、640／768和JPEG80均保持。
3. **已完成**全部帧与捕获／重连／排队记录。Graph 有端到端收益，但最大555／601ms，原500ms门槛仍未通过。相同静态来源下检测与距离一致，3张JPEG的时间相关文字差异已说明。
4. **已完成实验边界测试与交付检查**，检查和证据见本日服务实验记录。候选仍不接入默认服务；真实视频与真值继续等待同事交接。

后续优先对 Graph 路径剩余预处理、深度SDK处理／同步、跟踪和JPEG开销做分解，再选择能减少整帧时间的候选。当前实验中12帧窗口的排队仍是端到端延迟主因；不通过减小窗口、降采样或改门槛取得通过。真实移动视频的质量与实际负载仍需同事数据验证。

### 2026-10-20 继续执行顺序

1. **已完成**固定版本 SDK 调用链核对及配置合并、校验、弃用提示的独立计时；未改图像、模型或精度。
2. **已完成**独立复用官方 predictor 的候选，保留输入处理、预热、锁、回调、推理和后处理。配置或模型身份变化明确失败，不重建或回退；未叠加 Graph／排序候选。
3. **已完成**前置对照／候选／后置对照各31帧，原五张图正反序10次完整数值对照，以及同进程三组各31次调用开销测量；保留全部样本和准备成本。
4. **已完成决策与交付检查**：每帧约省0.2ms配置开销，没有证实稳定整帧收益，候选不接入默认服务；结论与检查见本日记录。真实视频和真值仍由同事提供，本轮不替代其质量验证。

上述SDK实验之后的深度 Graph 服务验证已完成，见本节前一份执行顺序。排序中位数和 SDK 外层配置不是当前排队问题的优先方向；正式质量准入仍等待同事视频与真值。

### 2026-10-19 继续执行顺序

1. **已完成**已保存浏览器逐帧阶段和排队耗时汇总，以及原公交样例的完整链路剖析。
2. **已完成**ROI 中位数独立排序实验。保留 FP32、检测640、深度768、JPEG80、30FPS上限、12帧窗口和原性能门槛；未启用深度 Graph。
3. **已完成**前置对照／候选／后置对照各31帧、五张样例正反序10次一致性对照、每张图三组各31次独立融合计时，以及异常／边界测试。候选没有整体收益，**不纳入默认服务**。
4. **已完成**结论、复现命令、必需检查和注释索引复核，见本日记录。真实视频及深度／距离真值仍由同事交接，本轮仅做静态诊断，不替代质量验收。

### 2026-10-18 继续执行顺序

1. **已完成**显式「常驻 GPU」页面模式：独立 `resident-v1` 准入校验、原二进制帧校验和有界流水线，保留 HF 的质量及配额契约。保持采集 640/JPEG80、30FPS 上限和 12 帧窗口。
2. **已完成**前端协议、模式选择、停止/错误、实际浏览器与本机 GPU 完整链路验证；记录全部帧，功能通过但原 ≤500ms 性能门槛未通过。
3. **已完成**公交图及 COCO8 原验证图的深度 Graph 多输入、不同画幅数值对照，保持 FP32 和原处理流程。候选仍只用于独立实验；未经真值质量验证不接入默认服务。
4. **等待同事数据**：用户确认真实视频及深度／距离真值尚未准备，由同事负责；仓库数据目录未发现该材料。不生成替代真值或把静态样例当成真实视频。
5. **已完成**仓库要求的检查及注释／索引复核；本轮日志保存于 `benchmarks/2026-10-18/`。

1. 常驻入口、串行服务层、有界二进制 WS、本机验证及页面 `resident-v1` 接入已经完成。结构与操作说明见 [RESIDENT.md](RESIDENT.md)。本轮没有发布到原远端服务。
2. 对深度 Graph 候选补充真实连续视频、不同画幅和深度质量数据，再决定是否接入服务；当前不自动启用候选、不改变精度或评估门槛。
3. 单用户完整流水线在较重输入下达不到稳定 24 FPS，继续优化前应使用真实目标视频确定处理预算；不通过改小图像、降精度、丢帧或放宽门槛隐藏排队。
4. 多用户调度、公网/真实手机、服务器部署与 VLM 外部服务实测尚未完成；当前证据仅覆盖本机单进程单活动会话。

### 常驻服务落地与验证（用户选择之后）

- 新增 `config/resident_asgi.py`、`realtime/services.py`、`realtime/resident_asgi.py`。原 Django/MCP/WS 入口保留，新 WS 路径为 `/ws/detect/resident/`。模型在启动时加载，所有 CUDA 推理运行于同一个线程，检测 Graph 跨会话复用，跟踪器每会话重建。
- 正常 stop 排空全部已接受帧，断连清空未执行工作并等待实际工作线程结束；服务不把 Python future 取消当作 CUDA 已停止。仍为 12 帧窗口和单视频槽位；无输入/发送阻塞各 15 秒，不沿用 HF 的 55 秒会话限制。
- 新增 7 项测试覆盖跟踪隔离与模型所有权、最终帧排空、真实线程阻塞下断连/重复取消、超额和乱序、Origin/Host/query、忙碌拒绝、启动失败及关闭确认时序。损坏 JPEG 只终止当前会话；非输入类推理失败使服务拒绝新会话，需显式重启。
- 首轮 4 次连接真实 GPU 实测交付 724/724 帧，只有一个模型实例和一个检测 Graph，4 个独立跟踪器。公交图与另一张 COCO8 图分别按 12/24 FPS 运行，换画幅仅发生在重连边界，全部记录的 `tracking_status=active`。
- 首轮退出时暴露 ASGI shutdown.complete 早于线程清理的问题；已经改为清理完成后再确认，并补充断言。原失败日志保留。修复后重复相同 724 帧时服务干净停止，但基准工具的最终状态字段拼写有误导致非零退出；拼写已修复，原报告和日志仍保留，不把该次命令称作整体通过。最终 60 秒测试同时验证了工具和完整关闭流程。

最终代码在同一本机执行 `--mode full --fps 12 --seconds 60`：

| 项目 | 结果 |
| --- | --- |
| 工作负载 | FP32 检测＋深度＋BoT-SORT＋运动／风险，未启用深度 Graph 候选 |
| ASGI 启动至模型就绪 | 4967.00 ms；第一真实帧仍另含初始化成本 |
| 帧数 | 首帧 1＋稳态 720；721/721 全部交付 |
| 稳态时间／吞吐 | 59.99 秒／12.0013 FPS |
| 端到端 P50／P95／最大 | 53.24／62.73／89.99 ms |
| 服务排队 P50／P95 | 0.0275／0.0705 ms |
| 最终状态 | completed，clean_shutdown=true，原 ≥10 FPS 且最大 ≤500ms 门槛通过 |

两轮短测公交图 24 FPS 的实交付约 22.14／22.17 FPS，最大延迟约 574.39／590.47 ms，**均未通过原门槛**，主要时间积累在排队。另一张图的处理负载较低、通过门槛；不能据此给所有输入做 24 FPS 保证。常驻重连公交图首帧约 47–49 ms，首次进程视频帧约 3.2 秒，证实模型与 Graph 复用确实消除了重复初始化；这些是静态样例测量，不能当作所有视频的首帧保证。

原始证据：[resident-full](benchmarks/2026-10-13/resident-full/report.json)、[resident-full-fixed](benchmarks/2026-10-13/resident-full-fixed/report.json)、[resident-full-60s](benchmarks/2026-10-13/resident-full-60s/report.json)。每个目录均保留所有帧 JSONL，无删除慢帧或自动重试。

### 2026-10-18 页面接入与多输入实验结果

- 页面增加「常驻 GPU · 80 类」，默认仍为 Echo；模式、来源提示及中英文文案同步。`LiveDetectionClient` 按调用方指定的 HF／resident-v1 契约校验，禁止交叉准入。常驻只接受原生 FP32 和 detection/depth/full 工作负载，每帧阶段必须匹配，正常结束必须确认全部请求／返回计数一致。
- 新增 5 项前端协议测试，加上现有模式／页面测试更新；后端167项、前端85项全数通过，无跳过。Django check、迁移检查、Ruff、pip check 和 git diff --check 通过；人工核对修改文件的说明与声明索引。
- `tools/benchmark_resident_browser.mjs` 使用真实 Chrome155、原页面与本机 GPU，仅以公开图片的 canvas stream 替代摄像头；限制每会话181帧，30FPS／12窗口及原模型／编码参数均不变。首轮静态画布停止供帧，未到推理连接，已保留失败并修正测试供帧；随后两次会话362/362帧全部显示，忙碌拒绝、重连、正常结束及手动停止释放均通过，页面无未捕获异常。

| 浏览器会话 | 交付／显示 | 首帧采集至解码 | 稳态显示 FPS | 稳态 P95 | 稳态最大 | 原性能门槛 |
| --- | --- | --- | --- | --- | --- | --- |
| 首次连接 | 181/181 | 6007 ms | 19.26 | 761.64 ms | 787.8 ms | 未通过 |
| 同进程重连 | 181/181 | 57.1 ms | 21.01 | 617.35 ms | 636.4 ms | 未通过 |

首次会话仅第一帧捕获检测 Graph，重连无重复捕获；所有帧跟踪为 active，窗口最大12。功能可用与性能验收分开：未调整采样率／窗口、丢帧、隐藏慢帧或启用未通过质量门槛的候选。Windows 测试工具仅终止其自建子进程，不将该清理当成 ASGI 优雅退出证据。

新增 `tools/validate_depth_graph.py` 对现有5张图按正序、逆序完成10次对照，覆盖 **4种** 实际张量形状（768×576、768×608、576×768、608×768，均 batch1／3通道）。每种形状显式建立实验 Graph，并验证复用：原始深度图、检测原始哈希、框／距离和完整JPEG全部一致，最大深度差为0。没有质量真值、真实移动视频或新增数据划分；此结果不替代深度准确率验证。

本轮证据：[浏览器精简统计](benchmarks/2026-10-18/browser-resident-draw/summary.json)、[浏览器全部帧](benchmarks/2026-10-18/browser-resident-draw/report.json)、[深度多输入对照](benchmarks/2026-10-18/depth-multi-input/report.json)、[检查日志索引](benchmarks/2026-10-18/checks.json)。真实视频与真值等待同事交接；远程部署、真机、公网及多用户调度仍未执行。

## 交付与证据

### 2026-10-20 深度 Graph 独立服务与浏览器实验

新增 `tools/resident_graph_asgi.py` 与 `tools/resident_graph_probe.py`，通过已有工厂接口装配原 `ResidentService`，默认 `config.resident_asgi` 不引用实验代码。只在首张真实图完整推理后准备一次固定形状Graph，采用原预处理、FP32与10次准备预热；原始深度输出必须逐值相同才完成捕获。首帧等待准备结束才发送，重连沿用同一Graph；错误不自动回退或重捕获。损坏输入保留原服务的会话拒绝规则，非输入类推理失败使服务不可用，需显式重启。

实验 metadata 的 `depth_graph_experiment` 明确记录 replay、capture_count、prepare_ms、张量形状和准备阶段一致性。`server_ms` 包含整个实验包装调用与准备时间，原流水线的计时另存 `pipeline_server_ms`；不会将额外首帧成本藏在帧间。SDK外层参数复用和排序中位数候选均未叠加。

实际 Chrome 三组按原实现／Graph／原实现顺序执行，使用相同公交图canvas流与原页面、30FPS上限、12窗口、检测640／深度768／JPEG80。每组两次181帧会话，合计 **1086/1086帧**完整接收并显示；各组另完成忙碌拒绝及手动停止，摄像头替身均释放、无页面异常、全部跟踪active。工具保持首帧单列，后续180帧全部计入统计：

| 实现／会话 | 首帧采集至解码 | 稳态 FPS | 稳态延迟 P95／最大 | 服务处理 P50 | 排队 P50 | 原门槛 |
| --- | --- | --- | --- | --- | --- | --- |
| 前置原实现／首次 | 4509.4ms | 17.40 | 718.34／748.90ms | 54.99ms | 603.45ms | 未通过 |
| 前置原实现／重连 | 59.9ms | 17.79 | 723.01／738.20ms | 55.17ms | 596.51ms | 未通过 |
| Graph／首次 | 4923.4ms | 22.82 | 545.11／555.00ms | 42.61ms | 470.08ms | 未通过 |
| Graph／重连 | 59.3ms | 21.28 | 590.35／601.40ms | 45.75ms | 490.54ms | 未通过 |
| 后置原实现／首次 | 4494.6ms | 17.52 | 709.93／723.10ms | 55.13ms | 597.92ms | 未通过 |
| 后置原实现／重连 | 60.7ms | 17.80 | 702.12／717.60ms | 54.75ms | 598.87ms | 未通过 |

Graph确实改善了本组静态端到端结果，仍未满足最大500ms。深度调用P50从对照22.46–23.47ms降至13.84／14.77ms；整帧尚需42.61／45.75ms，持续30FPS输入仍积累队列。不要把P50分布相加或将短测外推为尾延迟保证。

仅首次响应准备Graph，包含二次解码和预处理的准备耗时 **466.25ms**，其中Graph自身 **456.04ms**；形状为 `[1,3,768,576]`。后续361帧均使用replay，重连首帧 prepare_ms=0，未重复捕获。首帧总时间包含浏览器、检测Graph、SDK及深度Graph初始化，不能把它全部归因于深度Graph。

在相同源图、画幅和输入字节数下，两个对照与候选按帧序比较，检测原始哈希、目标身份、框与原始距离全部相同。候选相对前置对照有3张JPEG不同：一张由运动状态的最小时间跨度／确认时机造成；另外两张的速度回归浮点残差在约10⁻³¹m/s量级正负不同，标注显示为 `+0.00`／`-0.00`。原始字段与差异完整保留，没有改变阈值、时间戳或删帧强行一致。工具未对每个发送JPEG做输入哈希，因此不将该比较称为逐字节输入身份证明；此前五图深度Graph逐值一致性证据仍单独保留。

浏览器工具增加显式 `--depth-graph`；默认依旧选择原服务，并验证实验标记、仅一次捕获及重连复用。功能完成与性能通过分别记录：功能失败退出1，功能完成但原性能门槛不满足退出2，通过退出0；本轮三组退出均为2，不是性能通过。仅终止各次工具自建的Windows子进程，不将该清理当作优雅ASGI关闭验证。

从 `backend/` 复现，设置已有 `PRS_PLAYWRIGHT_PATH`，使用新目录：

```powershell
node tools/benchmark_resident_browser.mjs benchmarks/NEW_RUN/browser-before
node tools/benchmark_resident_browser.mjs benchmarks/NEW_RUN/browser-graph --depth-graph
node tools/benchmark_resident_browser.mjs benchmarks/NEW_RUN/browser-after
```

证据：[汇总](benchmarks/2026-10-20/graph-service/summary.json)、[Graph组全部帧](benchmarks/2026-10-20/graph-service/browser-graph/report.json)、[逐帧比较与差异](benchmarks/2026-10-20/graph-service/output-comparison.json)、[退出码](benchmarks/2026-10-20/graph-service/browser-exit-codes.json)。Graph重连页面截图已目视检查。默认服务与前端代码未改，候选不部署；真实视频／真值、真机、公网及多用户性能仍未验证。

交付检查：[checks.json](benchmarks/2026-10-20/graph-service/checks.json)。Django check、迁移 dry-run、后端 **177项**测试、Ruff、pip check、Node语法检查、git diff --check全部通过，测试无跳过；本轮四个代码文件已人工复核说明与声明索引，Python辅助AST检查通过。前端源码未改，没有重复前端单元测试，已执行上述实际浏览器验证。代码检查通过与浏览器性能门槛未通过分别记录；未提交、推送或部署。

### 2026-10-20 深度 SDK 调用开销实验

固定安装版本 Ultralytics 8.4.171 的 `engine/model.py` 会在每帧重新构造配置、执行 `get_cfg`，并把旧 `half=False` 参数转换为 `quantize=None`、输出弃用提示；模型及 predictor 本身已经复用。实验直接调用已完成首帧初始化的官方 predictor，保留它的锁、每帧数据源处理、同步计时、回调、推理和后处理，没有绕过 GPU 工作或删除模型阶段。

`tools/depth_predictor_probe.py` 检查原生CUDA、FP32、配置与模型／predictor／backend身份，配置变化时失败；只由显式工具选项启用。正常服务仍使用原 `DepthEstimator.predict`。首帧保持原调用，候选快照准备另计 **0.188ms**，不隐去初始化成本。

完整链路仍用公交图、独立进程、每组31帧（稳态30帧）：

| 实现 | 首帧（不含单列模型构造） | 稳态整帧 P50 | 稳态整帧 P95 |
| --- | --- | --- | --- |
| 前置原实现 | 5793.32ms | 62.83ms | 85.11ms |
| predictor复用候选 | 4491.39ms | 49.06ms | 58.07ms |
| 后置原实现 | 4597.09ms | 46.31ms | 55.94ms |

原实现前后波动明显，不能把候选相对前置对照的差值全部归因于优化。另用同进程原始／候选／原始各31次调用，测量外层调用与内部官方 predictor 的实际耗时差；不插入额外CUDA同步，不使用空模型：

| 调用 | 外层配置／分派开销 P50 | 完整深度调用 P50 |
| --- | --- | --- |
| 前置原实现 | 0.23870ms | 20.60ms |
| 复用候选（含契约检查） | 0.03525ms | 19.06ms |
| 后置原实现 | 0.23205ms | 19.98ms |

配置开销每帧仅减少约 **0.20ms**；各阶段分位数不能相加，剩余整帧差异不能据此认定为收益。该结果不足以解决约45ms处理时间和30FPS输入的积压，**暂不接入默认服务**，不宣称浏览器500ms门槛已通过。

原五张图片正反序共10次完整对照的深度图逐值完全相同、最大差0，检测、所有目标距离及JPEG均一致。完整模式三次运行的检测原始哈希、目标身份及原始距离均一致；前置原实现与候选的31张JPEG全部相同。两次原实现之间第5帧（index4）JPEG不同：实际帧时间不同，原有0.1秒最小历史跨度及两次确认使 `stationary` 状态在相邻帧生效，标注文字随之变化。该差异完整保留，没有改时间戳、阈值或删除样本来强行一致；详见原始字段诊断。

新增3项边界测试验证无推理的初始化、逐次传递新输入、嵌套配置／对象替换拒绝、非FP32拒绝及异常只调用一次。实际CUDA验证另由独立多输入工具完成，mock不用于宣称模型等价。

从 `backend/` 复现，输出目录必须不存在：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/NEW_RUN/full-before
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-cached-predictor --output benchmarks/NEW_RUN/full-cached
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/NEW_RUN/full-after
.\.venv\Scripts\python.exe -m tools.validate_depth_predictor --samples 31 --inputs model_tests/samples/bus.jpg models/experiments/coco8/images/val/000000000036.jpg models/experiments/coco8/images/val/000000000042.jpg models/experiments/coco8/images/val/000000000049.jpg models/experiments/coco8/images/val/000000000061.jpg --output benchmarks/NEW_RUN/predictor-multi-input
```

证据：[汇总](benchmarks/2026-10-20/summary.json)、[全部数值对照与调用计时](benchmarks/2026-10-20/predictor-multi-input/report.json)、[完整模式一致性及JPEG差异诊断](benchmarks/2026-10-20/full-equality.json)。工具记录原输入、权重、项目及SDK源码SHA；全部帧和慢样本保留。默认服务代码、前端、权重与评估参数本轮未改；未部署、提交或推送。

交付检查：[checks.json](benchmarks/2026-10-20/checks.json)。Django check、迁移 dry-run、后端 **173项**测试、Ruff、pip check、git diff --check全部通过，测试无跳过。本轮五个代码文件已人工复核注释／索引和行为契约，并以临时AST检查辅助核对；未引入文档检查工具。前端和默认服务本轮未改，未重复前端测试或浏览器实验；真实视频、深度真值、远端与真机验证仍未完成。

### 2026-10-19 排队归因与 ROI 融合实验

重新分析10月18日浏览器重连会话的180个稳态样本：服务处理 P50 **45.30ms**、服务排队 P50 **503.38ms**、采集至解码 P50 **563.50ms**。深度阶段 P50 22.62ms，跟踪7.03ms。分位数属于不同分布，不能相加；本轮未重跑浏览器，不能把静态调用结果当成新的页面成绩。

在 `ResidentDetector` 增加三个仅用于诊断的主机耗时：`depth_predict_ms`、`depth_fusion_ms`、`decode_boxes_ms`。三者之和保持原 `depth_ms` 的定义，没有增加 CUDA 同步；尚未完成的设备操作可能计入后续阶段，因此这些字段不是纯 GPU kernel 时间。模型、跟踪、融合算法和评估门槛均未修改。

原 GPU ROI 代码使用布尔索引构建变长数组，再取中间顺序统计值。独立算子记录中，公交图五个目标对应五次 `aten::nonzero`。候选把无效值置为无穷大、排序完整 ROI，再用设备上的有效样本数定位中间值；保留偶数样本先除2再相加、奇数样本直接返回的规则，未下载整张深度图。它消除了 `nonzero`，但引入完整排序和更多设备操作。

完整链路采用原公交图、独立进程、每组31帧（首帧单列，稳态30帧），按对照／候选／对照顺序：

| 实现 | 首帧 | 整帧稳态 P50／P95 | 融合 P50 |
| --- | --- | --- | --- |
| 前置原实现 | 4389.59ms | 47.73／53.44ms | 4.76ms |
| 排序候选 | 4291.12ms | 48.63／59.89ms | 4.91ms |
| 后置原实现 | 4304.04ms | 44.42／54.38ms | 4.43ms |

候选没有加速；原实现前后也存在运行波动，不能从这组短测宣称可靠尾延迟差异。93帧按序比较，检测原始哈希、目标身份／原始距离及JPEG完全一致；没有要求运行时间相关字段字节相同。首帧不包含单列的模型构造时间。

为隔离模型 forward 的波动，五张现有图片分别固定同一深度图和检测框，执行每组31次原始／排序／原始融合（首个另列），共 **465次**，所有输出完全一致：

| 输入 | 目标数 | 前置原实现 P50 | 排序 P50 | 后置原实现 P50 |
| --- | --- | --- | --- | --- |
| bus.jpg | 5 | 4.36ms | 4.77ms | 4.39ms |
| COCO8 000000000036.jpg | 2 | 4.31ms | 2.02ms | 3.53ms |
| COCO8 000000000042.jpg | 2 | 1.53ms | 1.92ms | 1.49ms |
| COCO8 000000000049.jpg | 6 | 3.07ms | 5.55ms | 3.01ms |
| COCO8 000000000061.jpg | 3 | 1.45ms | 2.69ms | 1.34ms |

五张中四张更慢。**拒绝将排序候选用于默认服务**，保留独立工具供复现负结果。五张图正序／逆序10次完整对照的检测、目标距离与JPEG均一致；CPU和实际CUDA的合成测试覆盖空有效集、NaN/Inf/非正值、奇偶计数、极小正数、极大值、非连续张量、裁剪与输出所有权。

从 `backend/` 复现（输出目录须不存在）：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/NEW_RUN/full-before
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-fusion-sort --output benchmarks/NEW_RUN/full-sort
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/NEW_RUN/full-after
.\.venv\Scripts\python.exe -m tools.validate_depth_fusion --samples 31 --inputs model_tests/samples/bus.jpg models/experiments/coco8/images/val/000000000036.jpg models/experiments/coco8/images/val/000000000042.jpg models/experiments/coco8/images/val/000000000049.jpg models/experiments/coco8/images/val/000000000061.jpg --output benchmarks/NEW_RUN/fusion-multi-input
```

剖析命令为 `python -m cProfile -o benchmarks/NEW_RUN/full-profile.pstats -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/NEW_RUN/full-profile`，须先建立父目录。cProfile/算子剖析会扰动耗时，其数据不参与上表性能对照。

证据：[汇总](benchmarks/2026-10-19/summary.json)、[浏览器阶段分析](benchmarks/2026-10-19/browser-stage-analysis.json)、[全链路一致性](benchmarks/2026-10-19/full-equality.json)、[多输入与独立融合全部样本](benchmarks/2026-10-19/fusion-multi-input/report.json)。原始基准逐帧 JSONL 与剖析输出均在同一日期目录；未隐藏慢帧、修改质量门槛、提交、推送或部署。现有30FPS页面的500ms门槛仍未通过，真实视频和真值仍需同事交接后再开展质量准入。

交付检查：[checks.json](benchmarks/2026-10-19/checks.json)。从 `backend/` 执行 Django check、迁移 dry-run、后端 **170项**测试、Ruff、pip check、前端 **85项**测试及 git diff --check，全部通过且无跳过。已人工复核本轮七个代码文件的说明、实际声明／限定方法索引及计时语义，并用临时 AST 检查辅助核对；没有引入 `tools/check_docs.py`。本机实际CUDA一致性通过不代表真实视频、质量真值或外部服务验证。

- 汇总：[summary.json](benchmarks/2026-10-13/summary.json)；全部基线、对照、传输和失败记录位于同一日期目录。
- 数值对照：[depth-equality.json](benchmarks/2026-10-13/depth-equality.json)、[full-equality.json](benchmarks/2026-10-13/full-equality.json)。
- 初期检查：[checks.json](benchmarks/2026-10-13/checks.json)，后端160项、前端80项通过。
- 常驻入口完成后的最终检查：[resident-checks.json](benchmarks/2026-10-13/resident-checks.json)，后端 **167 项全部通过、无跳过**；Django check、迁移检查、Ruff、pip check、git diff --check 均通过。前端本轮未改动，保留此前80项通过记录。
- 2026-10-18 页面接入后的检查：[checks.json](benchmarks/2026-10-18/checks.json)，后端 **167 项**、前端 **85 项**全部通过、无跳过；其余必需检查全部通过。
- 已人工核对新增/修改代码的说明与声明索引；未引入或绕过文档检查工具。
- 未创建 Git 提交，未推送或部署。模型权重、虚拟环境和下载缓存均在忽略路径中。
