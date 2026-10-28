# 服务推理优化计划

## 目标与范围

面向本机或自有服务器常驻 GPU，降低完整帧处理与浏览器端到端延迟。推理工作包括检测、可选深度、BoT-SORT、运动/风险、JPEG 编解码；同时关注启动、模型复用、排队、取消及会话隔离。

架构与操作入口见 [常驻服务说明](RESIDENT.md)。独立 Web/GPU 进程及视频告警入口见 [本地集成说明](LOCAL-MVP.md)。两种入口显式选择，不能混用 PRF1/resident-v1 与本地 PRF2 契约。

## 执行顺序

### Local browser-to-GPU demo validation

1. Verify the current local Web/GPU services and browser entry. Keep the original detector as
   the demo default; test the optional walk detector as a separate same-model comparison group.
2. Exercise the real file picker, native 1x video playback, canvas JPEG encoding, PRF2 WebSocket,
   local detection/depth/tracking/risk, returned JPEG decoding and rendered result panel. Use the
   existing day/night browser-compatible videos, with no simulated inference or cloud endpoint.
3. Compare Torch/GPU fusion, TensorRT/GPU fusion and TensorRT/host fusion, then repeat the original
   control. Keep input files, cadence, pending-frame bound, precision and algorithm rules fixed.
   Record cold first frame, every steady frame, delivered FPS, latency percentiles, source skips,
   server phases, speech-engine events, stop/EOF cleanup and isolated session identities.
4. Treat native playback sampling as an observed variable: retain source timestamps and admission
   counters rather than claiming identical captured frames across wall-clock browser runs. Reuse
   the separate complete-frame numerical evidence for exact-output claims. Capture actual result
   panels and inspect a visible local demo session; distinguish JPEG decode from physical display.
5. Fix any reproducible demo-blocking defect and rerun the affected checks with original failure
   evidence retained. Restore the verified original-model optimized local service, document demo
   operation and limitations, and report measured browser effects without changing acceptance gates.
6. If the full browser results reveal queue-bound latency, evaluate an explicitly selected
   three-job local-video admission limit against the original twelve-job limit on the same
   TensorRT/host service. Preserve the original default and all admitted frames; skip only before
   capture, retain source-coverage counters, and keep this policy experiment separate from model
   Forward claims. Check the original latency/FPS gates and complete EOF delivery in both modes.

### Optional walk model and native fusion optimization

1. Merge the published optional 87-class model and verify its checkpoint/manifest identity.
   Preserve the original `yolo26n` default, model-specific class/light rules and existing FP32
   TensorRT depth selection. Bind startup, advertised identity and lazy loading to one model.
2. Establish independent comparisons for each detection model on the same complete local videos.
   Reuse the verified depth engine and original frames/timestamps, FP32, max640/JPEG80 and risk
   parameters. Never compare different detection weights as an acceleration gain.
3. Evaluate the existing explicit host ROI reduction with the native TensorRT pipeline: one depth
   plane transfer can replace repeated per-object GPU reductions. Include transfer in fusion and
   full-frame timing, retain the original GPU fusion, and check every aligned map/object/JPEG.
   No detection thresholds, pedestrian-light rules, sampling, depth frequency or precision changes.
4. Apply the candidate through explicit startup configuration only if same-model controls support
   a gain and output consistency. Verify real PRF2 sessions for both model selections, then leave
   the original detector selected locally. Record all samples and limitations in structured reports,
   synchronize documentation/tests and complete the repository's delivery checks before pushing.

### Depth ONNX / TensorRT forward work

The executable workflow, artifact contract and final-weight handoff are documented in
[Depth forward export and acceleration](DEPTH-EXPORT.md).

1. Merge the teammate's upstream implementation, verify both supplied checkpoint hashes, and
   preserve original service contracts and existing experiment evidence.
2. Keep depth `imgsz=768`, batch 1 and FP32, with TF32 disabled as in the native inference plan.
   Prepare the existing bus/COCO8 fixtures through the pinned SDK; preserve every resulting tensor
   shape and value. Do not force square input or change preprocessing to improve a benchmark.
3. Export fixed-shape ONNX artifacts with checkpoint, graph, SDK and environment identities.
   Preserve the native quarter-resolution depth output and existing postprocessing. The SDK's
   standard export adds a full-resolution resize; record this contract explicitly instead of
   comparing unequal forward workloads. No FP16/INT8 conversion or numerical tolerance relaxation.
4. Build a native TensorRT FP32 engine per observed shape. Validate ONNX Runtime CUDA without CPU
   fallback, TensorRT bindings/buffer ownership and changed-input/shape rejection. Keep failures.
5. Compare PyTorch eager, the existing PyTorch CUDA Graph, ONNX Runtime CUDA and TensorRT using
   identical device-resident inputs. Separate construction/build, first call and the original
   30 steady samples after one cold call. Record CUDA events and synchronized host wall separately;
   retain every sample. Compare complete raw and aligned depth maps; report exact equality and
   observed differences without inventing a ground-truth acceptance threshold.
6. Retain explicit experimental adapters and re-export instructions for future fine-tuned weights.
   Bind engines to source/ONNX hashes, shape, precision, SDK/runtime and GPU identity. New weights
   require new export/build/validation; local consistency does not establish quality or deployment
   acceptance. Apply explicit native service selection through the integration work below;
   retain truth-based quality requirements for general deployment.

Export dependencies live in an isolated environment below the ignored local `.venv/` directory;
the existing native Torch/Ultralytics installation is reused read-only. Model binaries and engines
remain under ignored `models/experiments/`; reproducibility manifests and reports are tracked.
Implementation references: [Ultralytics depth contract](https://docs.ultralytics.com/tasks/depth),
[ONNX Runtime CUDA I/O binding and TF32](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html),
[TensorRT installation](https://docs.nvidia.com/deeplearning/tensorrt/latest/installing-tensorrt/install-pip.html).

### Apply validated acceleration to native inference

1. Keep the verified FP32 forward reports as the baseline; quantify forward and full-frame
   latency separately. Preserve all samples, source order, model parameters and quality limitations.
2. Move the reusable TensorRT implementation into the native inference package. Add an explicit
   startup backend/manifest selection, verified prebuilt shape dispatch, owned outputs and visible
   backend identity. Retain portable Torch configuration; never build or fall back inside a request.
3. Preserve the official SDK image preparation and depth alignment. Prepare missing artifacts for
   the existing local-video inputs, then compare complete original videos against Torch controls,
   including numerical depth/distance differences and derived tracking/risk outputs.
4. Exercise the actual local service, lifecycle and reconnect contracts. Enable the verified local
   configuration, record the full-frame effect, complete required checks, and commit source,
   configuration documentation and structured reports together. Ground-truth accuracy and final
   fine-tuned weights remain separate, explicitly unverified evidence.

| 顺序 | 工作 | 产出与准入条件 |
| --- | --- | --- |
| 1 | 同步远端代码并保持各服务接口兼容 | 保留常驻、HF、本地 PRF2、A/B/C 测距及视频/导航功能；通过规定检查 |
| 2 | 建立固定输入、原配置的可复现基线 | 分开模型构造、首帧与稳态；记录权重、输入、项目/SDK 源码身份和全部帧 |
| 3 | 剖析深度 Graph 的剩余完整帧开销 | 剖析前后各做无剖析对照；首帧/捕获不进入稳态 profile；拆分预处理、同步、后处理、跟踪及 JPEG |
| 4 | 依据实际瓶颈选择一个独立候选 | 原始/候选/原始对照；数值、缓冲所有权、失败传播和多输入一致性；不得叠加多个实验掩盖归因 |
| 5 | 在浏览器和连续视频链路验证 | 完整请求/返回计数、原始采集时间、跟踪状态、首帧及端到端延迟；保持原性能门槛 |
| 6 | 完成深度/距离及检测质量准入后评估默认接入 | 按既定数据划分和评估协议，用真值完成精度评估，再开展部署验证 |

## 上游模型与服务复验

同步上游后先核对模型清单和权重 SHA256，区分服务代码更新与新权重发布；Git 中未包含的模型不能当作已拉取的新模型。
保持现有参数依次完成接口合并检查、真实视频浏览器原始/深度 Graph/原始对照，以及本地 PRF2 的真实双模型会话检查。
PRF2 服务与 resident-v1 基准分开运行，避免同时占用 GPU 干扰计时；基准结束后保留明确选择的本地服务入口。
已有组合环境可显式用于两个独立进程，须核对各角色依赖及 CUDA；这不等于默认启动器要求的两个独立虚拟环境验证。
先确认最新代码的基线，再开展同帧 CPU 跟踪关联与深度 Graph 重叠候选；不因上游更新启用实验默认值。

## 固定实验条件

- 本机 CUDA、原生 FP32、batch 1、检测最大边 640、置信度 0.25、JPEG80、深度 imgsz 768；不得隐式采用 HF FP16 编译方案。
- 静态诊断复用公交图，每进程 31 帧；首帧单列，其余 30 帧全部进入稳态分布，包括慢帧。
- 浏览器原采样上限 30FPS、12 个在途作业；首帧独立完成。性能目标为至少 10FPS 且最大稳态延迟不超过 500ms，不通过时保留结果。
- 不改随机种子、数据划分、实验阈值、采样率或窗口来制造收益；不添加隐式重试、降级或丢弃请求。
- Graph 仅为独立实验，固定形状/类型/设备，变化即报错；捕获耗时计入首帧就绪成本。默认服务不导入实验入口。
- 模型权重和运行环境仍由本地准备，不提交凭据、缓存或模型二进制。

## 计时与证据规则

`forward_event_ms` 是检测 Graph 的 CUDA 事件时间；`inference_ms` 包含检测搬运和同步。`server_ms` 是主机上的完整帧处理时间，`resident_queue_ms` 是本机排队时间；浏览器采集至解码包含编码、传输、服务和解码。

深度诊断保持 `depth_ms = decode_boxes_ms + depth_predict_ms + depth_fusion_ms`。这些是无额外同步的主机耗时；融合还包括距离策略及缓存开销，C 模式跳过深度时 `depth_predict_ms=0`。异步设备等待可能计入后续阶段，不将其误称为纯 kernel 时间。

各阶段分位数不能相加。cProfile 会扰动 Python 调用与整体执行，只用于定位，不能当成吞吐成绩；CUDA 同步中的等待包含完成模型工作的必要成本，不能全部宣称为可删除的开销。保留所有调用及慢样本，失败不自动重跑，不以 mock 证明模型等价。

## 候选选择依据

| 候选 | 证据含义 | 使用边界 |
| --- | --- | --- |
| 深度 CUDA Graph | 静态多输入及两个完整视频的深度/检测/跟踪/风险/JPEG一致，视频离线整帧耗时降低；移动图源浏览器吞吐较高，但仍未通过最大 500ms 门槛 | 独立实验；排队主要等待前序帧处理，默认服务不启用 |
| 同帧 CPU 跟踪关联与深度 Graph 重叠 | 完整视频逐值一致且整帧耗时降低；现有街景浏览器样例通过原性能门槛 | 独立实验；最大延迟余量仍需关注，真值质量未验证，默认关闭 |
| 同帧精确主机深度融合 | 有界深度平面单次复制后做 FP32 精确分区；完整视频逐值一致，融合及整帧耗时降低，街景浏览器样例通过原门槛 | 依赖显式 Graph/重叠候选；真值质量与长期常驻待验收，默认关闭 |
| GPU ROI 完整排序中位数 | 样例一致，但多数 ROI 及整帧未加速 | 不接入默认服务 |
| 复用已初始化 SDK predictor | 每帧配置分派仅节约约 0.20ms，未证实稳定整帧收益 | 不作为当前主要优化方向 |
| 有界完整标签栅格缓存 | 静态重复图可减少绘制耗时；两个完整 MOT17 视频中命中率为 0，反而增加标注耗时，逐帧 RGB/JPEG 一致 | 保留独立诊断工具，不接入默认服务，不进入浏览器准入 |

标签缓存候选须将字体、字号、完整文字、颜色等渲染身份纳入键，限制内存并验证命中/未命中、边缘裁剪、中文、空距离、运动与风险文字的逐像素/JPEG一致性。移动视频可能使文字频繁变化，必须记录命中率和额外开销，不能将静态重复标签收益外推为视频收益。

### 标签缓存实验步骤

1. 保留原标签格式、背景、布局及 JPEG80，仅为显式工具增加文字绘制注入点。缓存完整文字的抗锯齿栅格掩码，包含字体对象/字号、文字、颜色、模式及绘制坐标小数部分；不取整或合并近似位置。
2. 使用最多 256 项、4 MiB 驻留栅格的 LRU，文字键限制 4096 UTF-8 字节；限额不包含临时栅格化和 Python/字体对象开销。单项超过容量或不支持的多行/字体模式显式失败，不降级。记录命中、未命中、驱逐、驻留/峰值栅格字节。
3. 比较实际 Pillow 编码前 RGB 与 JPEG 字节，覆盖连续命中、未命中、不同背景/画幅、四边裁剪、浮点位置、中文、空距离、运动、风险、字体身份及驱逐后重绘。
4. 原始/仅标签缓存/原始分别执行固定公交图 31 帧完整 GPU 链路；保持深度 SDK 原调用，避免叠加候选。首帧单列，保留所有稳态样本。
5. 对仓库两个原始 MOT17 预览按原顺序处理全部 500/654 帧，用源时间戳驱动跟踪，不抽帧；每帧将同一图像及同一实际检测/距离/运动结果交给原始、缓存、原始渲染，比较 RGB/JPEG 并记录绘制耗时和命中率。该额外绘制诊断不作为浏览器吞吐或深度准确率成绩。
6. 仅在静态整帧及移动场景均有收益时继续浏览器准入；否则保留负结果、默认关闭并根据证据选择下一任务。

### 移动视频对照与后续准入

标签缓存未满足移动场景收益条件。完整视频诊断保持缓存关闭、源帧顺序和时间戳、模型/距离/跟踪参数不变；剖析稠密场景瓶颈，逐帧比较输出，并以无剖析前后对照评估收益。不将降低深度频率、坐标取整、修改跟踪阈值或隐藏慢帧作为默认动作。

移动视频诊断按以下顺序执行：

1. 两个原始 MOT17 预览分别使用独立进程，完整读取 500/654 帧，以解码器源时间戳驱动跟踪；沿用 RGB、最大边 640、双线性缩放及 JPEG80。
2. 每个视频依次运行原始、Graph 无剖析、Graph 显式阶段剖析、Graph 无剖析、原始；首帧独立，后续所有帧保留。Graph 在首张真实帧后准备一次，准备成本计入首帧就绪，拒绝形状变化。
3. 在预测计时之外复制并散列完整对齐深度图，记录类型/尺寸以及验证开销；逐帧比对源输入、检测、深度、全部目标字段和 JPEG。对照采用相同源时间戳，不能用真实处理速度差异解释数值变化。差异保留并阻止准入，不调容差、阈值或随机种子。
4. 阶段剖析复用原 SDK 同步时钟，补充 BoT-SORT 更新、GMC、特征提取、光流及仿射估计；不增加 CUDA 同步，不修改 OpenCV 算法。前后无剖析 Graph 运行用于区分诊断扰动。
5. 若一致性与移动场景性能均支持收益，进入移动图源的浏览器验证；否则先调查差异或瓶颈。此工具的离线逐帧处理不等于实时采集、排队或浏览器延迟验收。

浏览器对照使用仓库现有 H.264 副本作为移动图源，执行原始/Graph/原始对照，保留原 30FPS/12 在途、首帧隔离、完整请求/返回计数和会话生命周期检查。记录媒体时间与实际采集时间，但不把它们误称为 resident-v1 的 GPU 跟踪时间；该服务仍使用工作线程时间。浏览器性能达标与真值质量验证完成前，不启用默认 Graph。

浏览器移动图源按以下步骤验证：

1. 为独立浏览器诊断增加显式 `--video` 路径，记录素材散列、浏览器版本和源码身份；通过诊断路由提供本地文件，不增加产品 API。原静态公交图模式保留。
2. 视频按原速播放，由浏览器视频帧回调绘制到摄像头替身 canvas，再经过实际页面的采集、JPEG、WebSocket、GPU 与解码链路。每次用户会话从开头开始，不循环、不暂停等待 GPU、不重放遗漏的源帧；若采集未完成而素材结束则失败并保留原记录。
3. 每个视频按原始/独立深度 Graph/原始各运行一个服务进程，每进程完成两次各 181 帧的会话及一次用户主动停止。验证忙碌拒绝、逐帧计数、跟踪、跨会话 Graph 复用和媒体释放，首帧单列，其余 180 帧完整计入原性能门槛。
4. 在采集入口记录源媒体进度、最近视频帧回调和接收 MediaStream 的时间观测；验证媒体前进及同一会话不倒退。这些观测不承诺被采集像素的精确 PTS。实时背压使不同运行采到的源帧不同，跨运行仅作端到端对照，数值等价依据仍来自固定输入的离线逐帧报告。
5. 汇总全部会话和慢帧；若最大稳态延迟仍超标，保留负结果，并从同一帧的编码、排队、处理、传输和解码分解选择后续调查。独立回归默认静态图源的兼容性，不将其混入移动对照。默认 Graph 仍须同时通过性能及质量准入。

短视频可能在冷启动后的指定采集数量完成前结束，必须保留该失败；不能将预定后对照当作对失败前对照的替换。MOT17-07 的原始前对照不完整，因此不声称该素材形成完整三组对照；MOT17-10 的完整对照及各素材全部慢帧见移动浏览器报告。实时背压下源帧不同，也不将浏览器吞吐差异直接等同于固定输入加速比例。

### 固定窗口下的尾延迟归因

移动浏览器证据显示 Graph 候选仍未达到最大延迟门槛，各会话最慢帧主要耗时来自服务队列。归因保持原采样、窗口、逐帧深度和性能指标：

1. 将每个慢帧的队列等待与同会话前序连续请求的服务耗时对应，使用同一帧的编码、排队、处理、传输残差和解码进行分解，不相加不同阶段的分位数。传输残差还包含序列化、校验及调度，不能误称为纯网络时间。
2. 先用现有逐帧元数据定位峰值；仅在证据不能解释时，增加显式浏览器阶段诊断，并在其前后运行无诊断对照。区分检测/深度 GPU 同步、BoT-SORT/GMC 与标注/JPEG；不将必要设备等待全部视为可删除成本。
3. 依据实际归因选择一个保持输出等价的实现优化。未获明确授权不得缩小窗口、降低采样或深度频率；数值、性能和质量准入均通过前保持默认 Graph 关闭。

队列归因工具先核对报告散列、计数、契约及计时源码，再仅使用同一会话的工作线程时钟。令当前帧原管线起点为 `S`，已记录队列等待为 `Q`，前一帧原管线终点至 `S` 的间隔为 `G`。FIFO 写协程只有在前一帧处理和发送结束后才取出下一帧，所以当前出队到工作线程起点的偏移 `d` 位于 `[0,G]`，真实队列区间位于 `[S-d-Q,S-d]`。前序原管线区间与该窗口的交集，在所有边界断点上求最小/最大值；不将 `S-Q` 冒充精确入队时间。

每帧保留前序请求序号、交集上下界、确定完整落入队列区间的前序帧及其互不重叠阶段总耗时；部分重叠的边界帧不按比例分配阶段。首帧无前序偏移界，单列并标为不可重建。原管线阶段和与 `server_ms`（Graph 时为 `pipeline_server_ms`）核对；Graph 准备、元数据组装、发送、空闲和线程调度等仍留在管线间隔中，不擅自归给某一阶段。失败或计时语义不匹配的输入明确报告，不能与完整会话混算。

现有记录的保守交集下界已经能解释超时帧的主要排队耗时，因此无需为这项归因重新运行浏览器或增加阶段剖析。该结果证明的是主机管线处理区间重叠，包含 CPU 工作及设备等待，不是纯 GPU kernel 占用。详细边界、前序帧及互斥阶段分布见队列归因报告。

### 同帧跟踪关联与深度 Graph 重叠实验

1. 保持单 GPU 工作线程、逐帧顺序和既有 Graph 候选，以当前串行 Graph 管线作为对照。只验证同一帧内提前提交深度计算、让 CPU `associate` 在设备工作期间执行；距离融合、运动历史和风险仍等待双方结果。不会跨帧并发、降低深度频率或改变跟踪算法。
2. 独立工具保留原深度预处理、推理和后处理、FP32、形状及输出缓冲所有权。现有 SDK 计时器会同步设备，不能仅将两行调用换序就宣称重叠，也不能删去必要的完成等待。首帧准备、取消后的工作排空、异常与形状变化须保持明确，禁止失败后自动回退。
3. 先检查可行性与故障边界，再在相同输入及源时间戳下执行串行/候选/串行对照，比较完整深度图、检测、ID、距离、运动、风险和 JPEG；不改变已有样例、视频帧序列或评估指标。重叠时单独记录 CPU、设备完成及整帧墙钟时间，不相加重叠阶段，也不能套用当前互斥阶段归因模型。
4. 仅在输出一致和完整帧收益均有证据时进入原 30FPS/12 在途/181 帧的浏览器门槛验证。跟踪阶段包含后续历史更新，其全部耗时不能当作可重叠时长或预期收益；默认服务与质量准入约束保持不变。

独立适配器 `tools.tracking_overlap_probe` 在已捕获 Graph 的推理回调内提交 GPU 工作，再执行原 CPU 关联；
返回 SDK 后仍执行原同步和后处理。完整深度图仅在同帧被消费一次，钩子每帧恢复；故障排空所属 CUDA 流并锁定失败，
不跨帧重用结果。`stage_timing_model=overlapping_cpu_depth` 表示深度调用墙钟包含关联时间，不能相加阶段或套用队列阶段归因。
`--trace-overlap` 显式增加 CUDA 事件和完成状态查询，成功路径不增加同步；事件耗时包含流上等待，不等于 kernel 占用或精确重叠时长。

固定源时间的验证顺序为串行 Graph、重叠、带设备观测的重叠、无观测重叠、串行 Graph；各次均处理两个原视频的全部帧，
以首个串行 Graph 为逐值参考。使用 `benchmark_video --depth-graph --tracking-overlap` 选择候选，设备观测另加 `--trace-overlap`；
候选不能同时使用互斥阶段的 `--trace-steady`。无观测对照用于判断整帧收益；观测结果只解释调度，不替代速度成绩。

浏览器分别使用两个视频的 H.264 副本，按串行 Graph/重叠/串行 Graph 执行同一套完整会话与停止检查。
命令为 `node tools/benchmark_resident_browser.mjs OUTPUT --depth-graph --video PATH`；候选显式增加 `--tracking-overlap`。
两个场景分别报告首帧、稳态和最大延迟；实时背压下的输入差异仍按上述证据边界处理，不据此声称逐帧加速比例。

故障验证还覆盖原 JPEG-only 输入边界：使用 Pillow JPEG 工厂避免 SDK 对通用图片入口的插件探测，
有效 JPEG 的 RGB 像素保持一致。输入错误发生在 GPU 工作前时允许明确的新请求；工作提交后的异常排空并锁定。
最终代码须重新完成源时间戳下的完整视频对照和浏览器门槛检查，旧版设备观测及暴露问题的失败报告均保留。

### 后续验收与调查

重叠候选的 CPU 开销诊断分开检测预处理、框转换、深度融合和标注/JPEG，
使用候选专用的重叠计时语义及前后无诊断对照。不得将旧互斥阶段总和当作当前瓶颈，或改变采样、窗口和精度来扩大余量。
执行顺序为完整视频重叠对照、`--trace-overlap-cpu` 显式主机诊断、完整视频重叠对照；
诊断保留 SDK 同步并分别记录 ROI、绘制文字/边界、JPEG 和预处理，嵌套时长不相加。
根据结果选择一个保持逐值语义的独立候选，先核对边界与完整视频输出，再通过原浏览器门槛；失败证据保留。
逐目标 GPU ROI 归约的独立替代实验为 `--host-fusion`，必须同时显式选择 `--depth-graph --tracking-overlap`。
该候选一次性复制本帧 FP32 深度平面到主机，按原正有限值过滤及偶数样本先除二再相加规则做精确分区，
复用原目标字段组装；全部复制成本计入融合与整帧耗时。仅支持原有界 FP32 CUDA 输入，不保留跨帧数据。
先比较奇偶数量、无效值、极大值、次正规值、非连续平面、边界框及异常恢复，再执行完整视频候选前后对照。
这项显式实验不改变默认 GPU 融合，也不改变图像、深度分辨率、采样、阈值或数值容差。
后续优先检查同一标签的文字边界测量与绘制是否重复布局，先核对当前 Pillow 实现；
不得更换字体、字号、布局、标注内容或 JPEG 参数。候选需逐像素/JPEG 对照，完整标签缓存的既有负结果仍保留。
模型/深度真值交接后按原质量协议评估，再决定默认接入与长期常驻验证；样例性能门槛通过不替代这些验收。

显式阶段诊断显示 SDK 推理结束后的同步在等待必要 GPU 工作，不能把全部等待算作可删除开销。当前环境的一份 cProfile 记录出现自耗时大于累计耗时等内部矛盾，保留原始记录但不用于归因；工具报告基础一致性异常，不自动修正数据或更换诊断方式。具体对照与阶段记录见下方报告。

## 数据与质量边界

静态输入为随附公交图及本机已有 COCO8 四张验证图，不重新划分或据此声称准确率。仓库现在包含 MOT17-07/10 的原始预览及 H.264 浏览器副本，见 [来源清单](model_tests/datasets/street-video/SOURCES.json)。这些素材可用于移动场景联调；来源记录未包含米制深度/距离真值，副本也不保证与原始预览逐像素一致。

深度/距离真值仍由同事交接，需提供 RGB/米制深度配对、有效掩码、视频时间戳/帧率/画幅及既定评估协议。检测准入沿用同条件至少三次完整 5000 图 COCO 评估、AP 降幅不超过 0.5 个百分点；检测 AP 合格不代替深度、运动或风险质量。

真机传感器、公网、外部 HF/DashScope、多用户吞吐及远程部署均需各自验证。工具中的 canvas 摄像头只覆盖浏览器和本机 GPU 链路。

## 操作与报告入口

在 `backend/` 中执行；输出目录必须不存在：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --output benchmarks/NEW_RUN/control
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-graph --output benchmarks/NEW_RUN/graph
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-fusion-sort --output benchmarks/NEW_RUN/fusion
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-cached-predictor --output benchmarks/NEW_RUN/predictor
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --label-cache --output benchmarks/NEW_RUN/labels
.\.venv\Scripts\python.exe -m tools.validate_label_cache --video model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4 --output benchmarks/NEW_RUN/labels-video07
.\.venv\Scripts\python.exe -m tools.validate_label_cache --video model_tests/datasets/street-video/mot17/MOT17-10-FRCNN-raw.mp4 --output benchmarks/NEW_RUN/labels-video10
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-graph --trace-steady --output benchmarks/NEW_RUN/trace
.\.venv\Scripts\python.exe -m tools.benchmark_inference --mode full --frames 31 --depth-graph --profile-steady --output benchmarks/NEW_RUN/profile
node tools/benchmark_resident_browser.mjs benchmarks/NEW_RUN/browser-graph --depth-graph
node tools/benchmark_resident_browser.mjs benchmarks/NEW_RUN/browser-video --video model_tests/datasets/street-video/mot17/MOT17-10-FRCNN-browser-h264.mp4
node tools/benchmark_resident_browser.mjs benchmarks/NEW_RUN/browser-video-graph --video model_tests/datasets/street-video/mot17/MOT17-10-FRCNN-browser-h264.mp4 --depth-graph
.\.venv\Scripts\python.exe -m tools.analyze_browser_queue --input-root benchmarks/2026-10-21/moving-browser --output benchmarks/NEW_RUN/queue-attribution
```

连续视频使用独立 `benchmark_video`，每个命令执行视频全部帧。例如对一个视频依次执行：

```powershell
.\.venv\Scripts\python.exe -m tools.benchmark_video --video model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4 --output benchmarks/NEW_VIDEO/before
.\.venv\Scripts\python.exe -m tools.benchmark_video --video model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4 --depth-graph --reference benchmarks/NEW_VIDEO/before --output benchmarks/NEW_VIDEO/graph-before
.\.venv\Scripts\python.exe -m tools.benchmark_video --video model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4 --depth-graph --trace-steady --reference benchmarks/NEW_VIDEO/before --output benchmarks/NEW_VIDEO/graph-trace
.\.venv\Scripts\python.exe -m tools.benchmark_video --video model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4 --depth-graph --reference benchmarks/NEW_VIDEO/before --output benchmarks/NEW_VIDEO/graph-after
.\.venv\Scripts\python.exe -m tools.benchmark_video --video model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4 --reference benchmarks/NEW_VIDEO/before --output benchmarks/NEW_VIDEO/after
```

对另一个视频换用其原始路径和新的输出根目录，不能跨视频或版本复用对照。`--reference` 核对输入/权重/代码/SDK/环境/配置身份后逐帧精确比较；退出 2 表示记录完成但存在差异，退出 1 表示执行失败。保留差异，后续独立对照用于归因，不代表重试失败候选。`predict_wall_ms` 的首帧包含深度 Graph 准备，`server_ms` 仍为原管线内部时间；后续深度图复制与散列单列 `depth_evidence_ms`。临时保留深度张量及诊断工作会影响资源占用，不能据此宣称服务显存、浏览器吞吐或最大延迟达标。

数值检查工具为 `validate_depth_graph`、`validate_depth_fusion`、`validate_depth_predictor`，须显式提供原输入与样本数；完整命令可用 `python -m tools.TOOL --help` 查看。

`--label-cache` 与深度候选互斥。`validate_label_cache` 读取完整视频，不抽帧；同一次真实推理的图像及结果依次交给原始/候选/原始渲染器，直接比较 JPEG 编码前 RGB 和 JPEG 字节。计时包含轻量编码引用捕获；额外三次绘制会扰动完整帧耗时，不能拿此工具的整帧或阶段诊断替代无诊断服务对照。静态分进程运行采用实际工作时间，运动确认可能在不同帧出现，须将这类标签差异与同输入渲染一致性分开核对。

`--trace-steady` 仅用于 full 模式，以临时包装测量原始阶段及 SDK 原有同步时钟；退出时恢复，输出 `stage-trace.json`。`--profile-steady` 输出完整 `profile.json` 与 `profile.pstats`。两者互斥，不计首帧、捕获和报告写入；都需要前后无诊断对照，不能把诊断扰动当作加速。

| 报告 | 用途 |
| --- | --- |
| [本机基线](benchmarks/2026-10-13/summary.json) | 检测、深度、完整模式与传输 |
| [常驻长测](benchmarks/2026-10-13/resident-full-60s/report.json) | 会话复用、计数、关闭及固定图片传输 |
| [浏览器基线](benchmarks/2026-10-18/browser-resident-draw/report.json) | 实际页面与固定 canvas 图源 |
| [Graph 多输入](benchmarks/2026-10-18/depth-multi-input/report.json) | 不同张量形状、缓冲复用及逐值一致性 |
| [ROI 诊断](benchmarks/2026-10-19/summary.json) | 负结果与全部融合样本 |
| [SDK 调用诊断](benchmarks/2026-10-20/summary.json) | 配置分派与多输入数值比较 |
| [Graph 服务诊断](benchmarks/2026-10-20/graph-service/summary.json) | 浏览器对照、捕获/复用及性能门槛 |
| [Graph 输出比较](benchmarks/2026-10-20/graph-service/output-comparison.json) | 检测/距离与真实时间导致的标注差异 |
| [Graph 剩余阶段诊断](benchmarks/2026-10-20/graph-profile/summary.json) | 显式阶段计时、前后对照、cProfile异常和下个候选依据 |
| [标签栅格缓存诊断](benchmarks/2026-10-20/label-cache/summary.json) | 静态对照、两个完整视频的像素/JPEG一致性、缓存边界与移动场景负结果 |
| [完整视频 Graph 与阶段诊断](benchmarks/2026-10-20/video-graph/summary.json) | 源时间戳下的完整深度/目标/JPEG比较、无剖析前后对照、GMC/光流/融合/绘制耗时及保留的预检失败 |
| [移动图源浏览器对照](benchmarks/2026-10-21/moving-browser/summary.json) | 原始/Graph/原始、全部端到端慢帧、媒体观测与会话检查、素材提前结束失败及静态兼容性边界 |
| [队列尾延迟归因](benchmarks/2026-10-21/queue-attribution/summary.json) | 同会话前序处理区间、未知调度偏移的保守上下界、完整前序帧阶段及下一候选依据 |
| [上游集成与模型服务复验](benchmarks/2026-10-21/upstream-model-service/summary.json) | 权重身份、三协议合并、移动图源原始/Graph/原始对照及真实本地 PRF2 视频会话 |
| [同帧跟踪与深度重叠](benchmarks/2026-10-21/tracking-overlap/summary.json) | 完整视频精确对照、设备观测、最终代码浏览器门槛、JPEG 输入边界修复与保留的失败证据 |
| [重叠管线 CPU 诊断与精确主机融合](benchmarks/2026-10-21/overlap-cpu/summary.json) | 嵌套阶段诊断、完整视频前后对照、包含传输的融合耗时、浏览器尾延迟及真实 GPU 故障验证 |

运行详情保留在结构化报告及逐帧 JSONL，历史报告描述其采集时的代码与状态，不能当作当前部署状态。

## 开发与交付检查

按 AGENTS.md 维护文件头、函数/类说明及实际声明索引；从 `backend/` 执行 `python manage.py check`、`python manage.py makemigrations --check --dry-run`、`python manage.py test` 和 `ruff check .`，前端变更执行 `node --test ../frontend/tests/*.test.mjs`。人工核对注释、索引和接口；检查结果与未验证项在交付时说明，详细日志写入报告目录。
