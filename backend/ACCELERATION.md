# 2026-10-05 纯推理最优配置迁移

用户将目标明确为推理本身的效率，并要求把长实验留在Kaggle。当前选择v8中三个独立
完整COCO确认的FP16＋Conv/BN融合＋channels-last＋torch.compile(default/fullgraph/static)
＋CUDA Graph，配合设备/锁页缓冲复用。v8纯forward三次中位数为1.710016、1.751632、
1.742272ms，组中位数1.742272ms；同版公平FP32 baseline为14.938976ms，约8.574倍。
三次AP均39.156308%，baseline39.170516%，下降0.014207个百分点，低于批准的0.5。
单次更快的候选不冒充三个独立完整质量确认的最优方案；原始数据和对照表继续保留。

v11在同一Notebook用两个全新T4进程恢复缓存，各20帧raw SHA与原v8逐位一致。
它是缓存/适配诊断：短测缺少同会话baseline和原计时重复条件，数值不与v8拼接排名。
HF初次发布f72689d2首调用8/8通过；第二调用复用同PID1371，已编译前端绕过FX查找，
清空后的计数为空而触发检查。这是复用处理/观测逻辑问题，不能称为JIT方法不支持。
修正为每次分配显式重置Dynamo前端，再恢复磁盘缓存；FX hit仍强制要求，新后端代码
生成仍禁止，模型/精度/布局/预处理和质量门槛不变。修正版HF提交eb063ebc；
同Notebook v12补充每PID两次恢复的诊断。HF只进行两个8帧短调用，结果及PRS激活身份
统一保存于`benchmarks/2026-10-04/hf-jit-migration-contract.json`。

官方依据：[Torch2.8 reset](https://docs.pytorch.org/docs/2.8/generated/torch.compiler.reset.html)
及[编译缓存接口](https://docs.pytorch.org/tutorials/recipes/torch_compile_caching_tutorial.html)。
HF可复用文件缓存，但每次GPU分配必须重建callable/Graph并核对硬件，不能跨GPU直接
复用T4二进制。以下是此前已完成的实验与AOT迁移历史，保留原始指标和失败。

# 2026-10-04 Kaggle GPU 重测（历史）

同一个私有 [Notebook](https://www.kaggle.com/code/heropig/prs-yolo26-gpu-rebenchmark-20261004)
通过版本管理完成实验：v7为全部单项/融合及前三组合确认，v8为实现/配置诊断与修正重测，
v9因序列化后的重复实验名称准入错误在测量前中止，v10保持条件重新确认实际HF服务模块。
v10已正常完成14报告，合格计划已部署到HF并验证真实GPU执行。v7/v8的完整速度、AP、样本和失败日志已经保存。

公平baseline包括eval、inference_mode、常驻模型、batch1、固定640及端到端免NMS头，
并共享原JPEG80编解码、直接FP32归一化、向量后处理和字体缓存。所有同版本候选对比该baseline；
CPU单项另与显式legacy CPU处理对照，以避免把baseline已包含的优化重复计为收益。
原权重、RGB/Pillow bilinear/letterbox、生产阈值0.25、输出300候选保持。
完整官方COCO val2017使用相同score0.001/maxDets100进行AP0.50:0.95评估；每个上线重复
均须覆盖5000图且AP下降≤0.5个百分点。官方train2017用于128图校准，与val不重叠。

v7的单项完整图片时延如下，包含解码至标注JPEG编码，不包含公网/ZeroGPU排队。
不同后端互斥，不能把它们当成可相加的组件。所有数值只与v7 baseline26.590ms比较。

| 方法 | 中位数ms | 观察 |
|---|---:|---|
| 公平FP32 baseline | 26.590 | COCO AP39.1705% |
| Conv/BN融合 | 20.120 | BN算子消除 |
| 可复用设备/CPU缓冲 | 25.492 | 单项收益小 |
| 锁页输入/输出缓冲 | 24.913 | 明确配对缓冲方案 |
| 可复用CPU预处理目标 | 25.327 | 算法及预处理不变 |
| channels-last | 26.882 | stride生效，单项未胜出 |
| cuDNN autotune | 25.000 | 不能由短测试宣称稳定小幅收益 |
| FP16单项 | 26.632 | 参数/卷积确为FP16，仍含BN，单项未加速 |
| CUDA Graph＋必要缓冲 | 15.925 | 保留固定shape、输入和输出所有权 |
| torch.compile default | 19.585 | 已执行编译 |
| torch.compile reduce-overhead | 15.955 | 内部Graph单独标记 |
| torch.compile max-autotune | 23.714 | 启动调优成本另列 |
| stock AOT | 14.582 | 包/导出/加载日志保留 |
| TensorRT FP32 | 15.343 | 实际engine精度审计 |
| TensorRT FP16 | 14.215 | AP39.1656%，质量合格 |
| TensorRT INT8：4图／128图 | 13.338／13.314 | AP35.1232%／33.4132%，原配置质量不合格 |
| OpenVINO GPU／FP16 hint | 32.488／32.478 | v8另验证实际FP32 GPU算子 |
| 原生BF16／TF32 | 不适用 | T4原生能力不支持；非原生BF16另测 |
| 非原生BF16 | 37.147 | 实际BF16路径，AP38.7057%，单项较慢 |

ORT CUDA图优化NONE/BASIC/EXTENDED/ALL、arena/memory pattern/I/O Binding、设备池配置、
FP16/INT8及Graph组合均有独立记录。旧流配置的质量异常不作为方法失败：v8修正后
ORT_ALL16.072ms/AP39.1706%，融合Graph15.623ms/AP39.1706%；只与v8 baseline25.683ms
计算1.598／1.644倍。160次受控流诊断中，旧eager/Graph各有19/20次自然调用错误，
人为延迟时各20/20错误；修正两条路径的自然/延迟共80次调用均一致。压力调用不进时延/AP排名。

v7最佳已测JIT融合为FP16/channels-last/Conv-BN融合/缓冲/Graph/default编译，三个独立
完整AP均39.1563%，组合中位数11.671ms，对同版本baseline为2.278倍。v8重测同组合
12.358ms，串行stock AOT FP16/contiguous Graph为12.432ms/AP39.1569%，对v8 baseline
为2.066倍。不同版本时延没有拼接排名；约0.6%的组合差异不证明统计显著。
当时尚未实现JIT缓存的跨分配复用，因此先验证并部署AOT服务模块；该限制已在上方
缓存迁移工作中重新检验，不能作为JIT无法部署的一般结论。

诊断证据按实现、配置、后端顺序保存：

- FP16确已执行，但单项仍有114个BN、五帧4045个kernel；融合消除BN，编译融合降至1215个kernel。
  内核计数不证明Tensor Core指令比例或唯一瓶颈。
- OpenVINO权重转换成功，GPU profiling仍为FP32 Conv/GEMM；不能把文件精度称作实际半精度计算。
- ORT INT8严格审计发现CPU浮点反量化；优化器配置和同图完整AP另设控制，不能忽略CPU计算。
- TensorRT原INT8配置实际有97个INT8加权层，但完整AP不合格；v10额外使用同校准图的strong
  typing、无FP16/INT8 builder flags对照。v10完整AP32.8600%，同图ORT禁用优化器AP32.9314%，
  对baseline39.1705%分别下降6.3106／6.2391个百分点，两独立后端均不合格；这说明当前
  全图PTQ配置的量化误差不能靠上述配置修正消除，不说明所有INT8策略无效。
- Spaces AOT未融合时的整数BN状态排空错误、默认AOT loader在外部Graph中的事件查询错误均有
  原始异常；BN融合与stock run_single_threaded=True的独立对照验证配置修正。
- v8所有57个报告完成后，图表渲染因继承无效MPLBACKEND而失败；原summary未改变，已以明确Agg
  配置重新生成并检查图表，v10包含这一配置修复。

原始数据与完整逐项表：`benchmarks/2026-10-04/kaggle-v7/output/results/summary.json`、
`kaggle-v8/output/results/summary.json`及各版本`report/evidence-report.md`。
每种失败保留report/process/worker日志、实际配置及执行图；源文件、原权重、校准/验证图和
预测均有SHA记录。报告同时保留初始化、Graph准备、P95、原始样本及baseline漂移，
结论是已测条件内的最优候选，不宣称所有可能组合的全局最优。

## v10 实际服务模块确认

最终三个服务配置各三次独立完整COCO：公平baseline23.811608ms，原FP32 Graph服务
14.411271ms，FP16 Graph12.559066ms，串行AOT FP16 Graph11.725171ms。最优兼容方案对
公平baseline为2.030811倍，对原服务为1.229088倍（处理时延减少18.64%）。三个AOT AP
均39.156946%，baseline39.170516%，降幅0.013570个百分点；预测SHA均561228d6开头。
末尾baseline24.362520ms，比初始慢2.31%，原样记录，不做时延校正或删样本。
最终summary SHA d6fc3d933c390867abf3acb68c120c29bc11c3029b540ebfe846899cc739a5bd。
models/inference-plan.json仅来自此版实际serving_adapter。HF提交78f5bf06已在实际Blackwell
GPU上重新编译AOT并执行，18个发布文件的Git blob身份与固定提交一致；未直接迁移T4二进制。
startup编译45.748秒、首次分配加载1.186秒、Graph准备1.847秒与稳态帧分别记录。
两次独立调用均16/16交付，四图诊断13/13参考框匹配；这不替代HF完整COCO验证。
公网Chrome短测9.772FPS、最大解码448ms；55秒自然结束测试571帧提交/566帧返回，
5帧在途未返回，最大解码566ms，公网10FPS/500ms及完整交付验收未全部通过。
GPU计算、Graph复用和返回帧哈希正常；硬截止与持续采样的边界以及公网等待另见
[PHONE-LIVE.md](PHONE-LIVE.md)，没有删帧、延长预算或自动重试来制造通过。

以下是原CPU与旧接口的历史实验。其条件、原始报告和失败记录保留，不作为现行GPU结论。


# YOLO26n 推理加速比较与融合

2026-10-02：保持官方 YOLO26n 权重、FP32、640×640、置信度 0.25、JPEG 80、客户端 200 ms 间隔及服务端 20% CPU / 256 MiB / 32 tasks 限额，选择有上限的 ORT CPU 内存池 + I/O Binding + 可复用输入/输出张量 + 直接 FP32 归一化 + NumPy 批量后处理 + 字体缓存。原有 ONNX、固定形状、单会话和端到端免 NMS 继续使用。

用户允许测试 INT8/FP16，但须验证后再决定上线。实测它们未超过融合 FP32，因此此次仍部署 FP32。模型权重哈希和检测/图像参数不变，不加入自动后端切换、降级或重试。

## 实测结果

服务器：Ubuntu 22.04、2 个逻辑 CPU（同一物理核的超线程）、Intel Xeon Platinum；支持 AVX2/AVX512，无 GPU，CPU flags 中无 VNNI/BF16。所有实验串行运行，临时 systemd 单元均限制 CPUQuota=20%、MemoryMax=256M、TasksMax=32，OpenBLAS/OMP 单线程，含 Django。PRS 的正常服务保持运行，其他应用未停机；背景负载和 CPU 限流会影响短测试的方差。

每组公开公交车图：1 次不计时预热、10 次测量，包含 JPEG 解码、预处理、推理、后处理和绘制/编码，不含网络和手机采集。另使用 COCO8 的 4 张 val 图对照输出。基线实现来自提交 `107e7e0`，中间候选使用其预/后处理；production 使用最终实现。原始 JSON 见 [benchmarks/2026-10-02](benchmarks/2026-10-02/)。

| 方案 | 平均总处理 ms | 中位数 ms | P95 ms | 平均 CPU ms | RSS 峰值 MiB | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 原 FP32 / arena 关闭 | 625.3 | 608.6 | 746.0 | 130.3 | 192.8 | 基线 |
| FP32 / 默认 arena | 551.7 | 546.2 | 606.6 | 111.4 | 221.3 | 减少分配开销 |
| FP32 / 仅绑定和缓冲复用 | 641.2 | 613.5 | 701.0 | 129.6 | 179.8 | 单独绑定无明显加速 |
| FP32 / 默认 arena + 绑定 | 549.8 | 509.9 | 648.2 | 109.8 | 222.5 | 有效组合 |
| FP32 / 128 MiB arena + 绑定 | 560.5 | 548.8 | 655.3 | 112.6 | 222.8 | 明确内存池上限 |
| **最终 FP32 融合** | **509.7** | **502.2** | **555.6** | **102.2** | **220.5** | **上线候选** |
| FP16（保留头部 Cast） | 608.6 | 603.2 | 693.7 | 120.8 | 228.4 | 比融合 FP32 慢 |
| INT8 QDQ / per-channel | 1161.8 | 1193.8 | 1252.1 | 233.8 | 218.8 | 此配置明显更慢 |
| OpenVINO 2026.4.1 / 强制 FP32 | 572.4 | 553.2 | 695.0 | 116.7 | 246.1 | 较慢且内存余量小 |

最终组合相对基线平均处理时间减少 **18.5%**，CPU 时间减少 **21.6%**。这只是固定样例的短资源测试，不是统计显著性结论、COCO 准确率评测或手机 FPS 保证；4 张图不能证明所有输入相等或识别精度。网络、手机编码和既有 200 ms 间隔仍在，预计实际回传约 1 fps。

公交车图：最终组合与基线的原始预测及 JPEG SHA256 相同，均返回 5 个目标。4 张 COCO8 val 图：最终组合 raw SHA256 全部相同，13 个基线预测框均保持。FP16 对应 13/13，置信度平均绝对差约 0.00155；OpenVINO 13/13，差约 4.7e-7；INT8 对应 12/13，少一个基线背包预测，差约 0.0802。这里以同类别、IoU≥0.5 做贪心输出对照，**参考是 FP32 预测，不是标注真值**，不能称为 recall、precision 或 mAP，也不能断言背包一定是真实漏检。见 quality.json 和 production-quality.json。

INT8 用 COCO8 train 的 4 张图进行 MinMax 校准，val 不参与校准；QDQ、S8S8、per-channel，仅 Conv/MatMul。校准规模不适合生产质量验收。FP16 固定 FP32 I/O，保留原 Cast 节点以避免转换器错误的类型注释，转换后执行 ONNX 完整检查。两种实验都没有替换生产模型。具体慢因尚未做逐算子诊断，不能仅根据“INT8”名称推断更快。

## 可兼容方法和适用边界

下面区分已存在、本次融合、已测试和后续候选；没有把互斥后端称作可同时叠加。

| # | 方法 | 本系统处理 |
| ---: | --- | --- |
| 1 | PyTorch 导出 ONNX，避免训练框架在线运行 | 原有 |
| 2 | 长期复用 InferenceSession | 原有 |
| 3 | 固定 640 形状、batch=1 | 原有；适合低延迟单帧 |
| 4 | 常量折叠 | ORT_ENABLE_ALL 原有默认，现显式声明 |
| 5 | 冗余 Identity/Reshape 等节点消除 | 同上，由 ORT 按图适用性处理 |
| 6 | Conv/激活、矩阵等算子融合 | 同上；不声称每种融合都在本图触发 |
| 7 | CPU NCHWc 布局优化 | 同上；实际后端按硬件选择 |
| 8 | 权重预打包 | 保持 ORT 默认机制；未单独量化收益 |
| 9 | 静态形状 memory pattern | 原有默认，现显式保持 |
| 10 | CPU arena 复用 | 本次启用并验证 |
| 11 | 内存池按申请大小增长、设置容量上限 | 本次；128 MiB 模型池，系统总限额不变 |
| 12 | 输入张量预分配和复用 | 本次；保持连续 FP32 NCHW |
| 13 | 输出张量预分配和复用 | 本次；固定 [1,300,6] |
| 14 | I/O Binding | 本次与内存池结合；CPU 情况不是 GPU 拷贝加速 |
| 15 | 直接 uint8→FP32 归一化到目标缓冲 | 本次；避免中间 FP32 数组/拷贝 |
| 16 | 转置视图与归一化一起写入连续目标 | 本次；颜色顺序、缩放和填充不变 |
| 17 | 批量校验类别/分数、先筛选再生成字典 | 本次；低分无效数据仍报错 |
| 18 | NumPy 批量还原/裁剪框 | 本次；float64 对齐原 Python float 运算 |
| 19 | 缓存同一字体 | 本次；字号、颜色和绘制契约不变 |
| 20 | 单线程、顺序执行，控制 OpenBLAS | 原有；保留资源隔离 |
| 21 | 禁止额外线程空转 | 本次显式配置；单线程时不宣称有额外加速 |
| 22 | YOLO26 one-to-one 免 NMS | 原有；不加入外部 NMS |
| 23 | JPEG 原生编解码库 | 保持 Pillow；未替换为不同预处理库 |
| 24 | 不写磁盘、不存媒体 | 原有，避免帧 I/O |
| 25 | 单个在途帧、不积压队列 | 原有，避免显示过时帧；不丢帧伪造 FPS |
| 26 | 离线优化图、ORT 格式、编译缓存 | 后续启动优化；长期会话稳态收益不能重复计算 |
| 27 | ONNX simplify/图手工精简 | 后续候选；与既有图优化可能重叠，须验证输出 |
| 28 | 预热 | 实验使用；生产未用合成帧冒充摄像头或额外启动计算 |
| 29 | OpenVINO / oneDNN CPU 后端 | OpenVINO 实测未胜出；oneDNN 未测，不同时上线两个引擎 |
| 30 | FP16 / BF16 / 混合精度 | FP16 实测未胜出；BF16 缺相应硬件支持，需另验证 |
| 31 | INT8 PTQ、QDQ/QOperator、不同校准方法 | 当前 QDQ 未胜出；其他表示/校准是独立候选，不能自动替换 |
| 32 | QAT、结构剪枝、蒸馏、更小模型 | 需要训练及质量评测；Nano 已是本系列轻量型号 |
| 33 | 缩小输入、动态尺寸、ROI 裁剪 | 可能减少计算，也可能漏目标；未改变 640 参数 |
| 34 | 提高阈值、减少 max_det、类别预筛选 | 会改变输出目标；未改变现有契约 |
| 35 | 多线程、并行图执行、绑核/NUMA | 需结合限额测试；当前仅一个物理核，无专用核，未抢占其他应用 |
| 36 | 批处理、多 worker、流水线并发 | 偏吞吐，可能增延迟/内存；未改变单帧/单 worker 边界 |
| 37 | CUDA / TensorRT / CUDA Graph / GPU I/O Binding | 需要 NVIDIA GPU，当前服务器无 GPU |
| 38 | XNNPACK / NCNN / TVM / 自定义 SIMD/C++ 算子 | 新构建与兼容验证成本；未测，不宣称收益 |
| 39 | CoreML / LiteRT / NNAPI / QNN / 手机 NPU | 改为端侧推理，是架构方案；需各手机支持和质量验证 |
| 40 | 浏览器 WebGPU / WASM SIMD / WebNN | 端侧候选，兼容性与电耗需验证 |
| 41 | WebSocket 检测、HTTP 连接复用、二进制协议 | 网络层候选，不能减少 CNN 计算；现同源二进制 JPEG |
| 42 | 返回框由浏览器绘制、降低 JPEG 质量/帧间隔 | 可减传输或提高显示频率，但改变现有返回标注图/采集参数，未实施 |
| 43 | 结果缓存、跳帧、跟踪代替部分检测 | 改变实时识别语义；未添加 |
| 44 | 增配 CPU、独立推理服务器/GPU、提高 CPUQuota | 最大资源空间，但需要明确的新资源预算；未实施 |

依据：[ORT 图优化](https://onnxruntime.ai/docs/performance/model-optimizations/graph-optimizations.html)、[线程调优](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)、[I/O Binding](https://onnxruntime.ai/docs/performance/tune-performance/iobinding.html)、[allocator](https://onnxruntime.ai/docs/get-started/with-c.html)、[量化](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html)、[OpenVINO 精度](https://docs.openvino.ai/2026/openvino-workflow/running-inference/optimize-inference/precision-control.html)、[Ultralytics 部署格式](https://docs.ultralytics.com/guides/model-deployment-options)。文档列的是能力和候选，不替代本机实测。

## 历史记录

以上记录来自当时的独立实验，数值、数据划分与评估口径保留。未上线的低精度/OpenVINO实验执行工具已移除，原实现可从 Git 历史查阅；当前运行只保留 Echo 和最新 HF ZeroGPU 视频路径。原始 reports 继续保留于 benchmarks，以便溯源。新的同类框一致性验证使用 `tools.benchmark_live_remote` 和 `tools.compare_detections`，不是重新执行旧后端对照。

## Kaggle GPU 原子实验与组合实验

2026-10-02 用户授权在 Kaggle 测试加速方法，再将有效且兼容 ZeroGPU 的方案部署到 HF。私有 Notebook 为 [原子实验](https://www.kaggle.com/code/heropig/prs-yolo26-acceleration-20261002) 和 [组合实验](https://www.kaggle.com/code/heropig/prs-yolo26-refinement-20261002)。私有数据包由 `tools/prepare_kaggle.py` 的明确清单生成，仅包含推理/基准源码、固定官方权重、公开 bus 图片及原有四张 COCO8 val 图；不上传凭据、私人图片或整个项目。

复现入口为 `python tools/prepare_kaggle.py --phase atomic` 或 `--phase refinement`，生成文件在排除 Git 的 `.kaggle-bundle` 内，再通过已登录 Kaggle CLI 上传私有数据版本并提交 GPU Notebook。Kaggle 自动解压上传 ZIP，入口读取挂载文件；首版错误地查找 ZIP，因此在推理前失败，第二版修正该输入契约。该失败不计入速度结果。组合实验还显式配置现有 CUDA 驱动的链接名称，修复 AOT 编译器找不到 `-lcuda` 的环境问题，不更换驱动或模型。

原子实验设备 Tesla T4，PyTorch 2.8.0+cu128 / CUDA12.8，GPU0 单卡、CPU 数值处理单线程。各配置独立子进程；同一公开 bus 图片，10 次测量前预热，3 轮，每轮30次 GPU 阶段和12次完整图片处理。GPU 阶段包含输入上传、推理和输出下载；另用 CUDA event 测量 forward。完整图片处理还含解码、原有 Pillow 预处理、阈值筛选、绘制及 JPEG80 编码，不含 Gradio/网络/排队。setup 包含初始化、预热及可选编译/捕获，单独记录；首次 eager forward 的单次耗时不能当作冷启动统计。不同轮次的环境缓存和负载可影响结果，不将小幅差异认定为稳定收益。

原始完整报告为 `benchmarks/2026-10-02/kaggle-atomic.json`。首轮 GPU 阶段/完整处理的中位数分别为：FP32基线9.377/19.467 ms，缓冲复用9.085/19.096 ms，CUDA Graph+缓冲复用5.192/14.846 ms。后者四张图 raw预测4/4逐位一致，13个参考目标同类IoU≥0.5匹配13/13。channels-last、cuDNN自动调优、FP16、BF16均未在本轮超过该方案；T4没有原生TF32/BF16计算能力，BF16结果包含运行时支持的非原生路径，不能外推到Blackwell。AOT初次失败为链接器缺少libcuda.so名称，保留失败记录并在独立组合实验中重新验证。

固定权重、batch1、640、0.25阈值、300候选、JPEG80及验证图片保持不变。低精度仅在明确命名的实验配置使用。四张图的预测一致性不是mAP/真实视频准确率；GPU热运行结果不包含ZeroGPU每次任务的进程与GPU调度成本。Kaggle编译二进制不得直接部署到不同GPU的HF。生产选择与实际HF验证另行记录。

组合实验完整报告为 `kaggle-refinement.json`：同轮基线GPU阶段/完整图片为9.685/19.394ms，FP32 Graph为5.051/14.500ms，AOT为5.302/14.504ms，AOT+Graph为5.092/14.257ms。FP16+channels-last+Graph完整图片12.983ms，但置信度存在约0.00164的平均差异，未改变生产FP32。以上均匹配13/13参考框，仅Graph路径raw预测逐位一致。精确 `spaces.aoti_compile/aoti_apply` 兼容性实验见 `kaggle-zerogpu-compatibility.json`，也验证了13/13一致性，不能代替实际ZeroGPU验证。

实际HF硬件日志为 RTX PRO6000 Blackwell Server Edition MIG2g.48gb。AOT首版因非连续anchors常量打包失败；在导出前仅将anchors存储连续化后成功运行，四张原图13/13参考框匹配，平均置信度差4.79e-7。但单帧GPU阶段1415–1960ms，高于eager首张438.7ms。因此显式撤回AOT，原单帧恢复eager，未添加自动切换。还原后原有四帧检查前三张GPU阶段510/562/602ms，第四张超过既定55秒网络截止时间，完整验证未成功；失败未重试。不能把部分结果记为完整成功。

用户随后明确选择“一次GPU任务内复用CUDA Graph和缓冲区，保留原逐帧接口”。新增独立 `/detect_clip`，最多16张有序JPEG，batch仍为1，不跳帧；GPU任务内10次预热、捕获、复用固定输入/输出缓冲，任务结束销毁Graph，原 `/detect`、`/status` 不变。客户端仍55秒；只有新片段验证工具显式使用24MiB响应上限，原单帧仍2MiB。

[片段实验](https://www.kaggle.com/code/heropig/prs-yolo26-video-clips-20261002)使用三个独立进程轮次，原四张图循环组成片段，计入预热和Graph捕获，模型根初始化在两个策略中均排除。首版绝对路径执行缺少模块搜索根，在推理前失败；修正导入路径后第二版完成，未修改输入、warmup或比较方式。报告 `kaggle-video-clips-v1-failed.json` 和 `kaggle-video-clips.json` 都保留。

| 帧数 | eager GPU任务中位数ms | Graph GPU任务中位数ms | eager/Graph | 所有轮次raw一致 |
| --- | ---: | ---: | ---: | --- |
| 1 | 493.639 | 678.764 | 0.727 | 是 |
| 4 | 551.134 | 711.709 | 0.774 | 是 |
| 8 | 561.838 | 743.555 | 0.756 | 是 |
| 16 | 656.897 | 818.014 | 0.803 | 是 |

因此短片段中，热态Graph收益没有抵消约667–685ms的初始化。重复静态图用于接口和输出验证，不是视频数据集/mAP/FPS承诺。HF服务端日志显示4帧GPU任务868.2ms（setup854.7ms）、16帧863.2ms（setup816.0ms）；任务确已执行，但两次客户端接收超过55秒。独立8帧验证也在接收阶段超时，只收到81907字节。16帧POST取得事件号约2922ms，说明该次失败发生在结果接收而非提交。未证明具体网络/托管根因，未增加超时、压缩、丢帧或响应降级。部署及失败报告见 `hf-kaggle-deployment.json`、`hf-video-clips.json`、`hf-video-16-frames.json`、`hf-video-8-frames.json` 和经过筛选的 `hf-clip-server-log.json`。

目前部署的是可实验的Graph片段接口，尚未证明55秒预算下的端到端加速；现有手机逐帧入口仍按原接口运行。更长片段、减少初始化预热、换返回格式或常驻GPU会改变当前边界/实验条件，需要明确决定后独立实验，不能从热态数据直接上线这些变更。

## 已批准的独立长片段实验

用户明确批准新增32/64/128帧实验，保留上述短片段条件和结果。Kaggle三个独立轮次的含初始化GPU任务中位数（eager/Graph，ms）为32帧813.749/869.644、64帧1089.045/1048.759、128帧1678.652/1365.432。128帧为1.229x；64帧收益很小。所有轮次逐帧raw哈希一致。报告 `kaggle-video-long-clips.json`，Notebook https://www.kaggle.com/code/heropig/prs-yolo26-video-long-clips-20261002 。

HF独立公开图片诊断完成：16帧eager/Graph GPU696.813/770.603ms；128帧1216.825/678.289ms（1.794x），Graph初始化310.472ms。所有128帧raw和渲染JPEG哈希逐帧一致；原四张图13/13参考框匹配。这是单次观察，且只传元数据，不是完整视频交付或稳定延迟分布。完整报告 `hf-public-clip-profile.json`。

新增显式 `/detect_video_stream` 传输协议：同样的JPEG和FP32处理，一次Graph任务后以不超过16KiB的有序片段传输完整JPEG和框；客户端逐帧校验JPEG及最终完成事件。最多128帧，仍55秒，不替换旧接口、不降级、不重试。传输实测报告另存 `hf-video-stream.json`；计算成功不能代替接收成功。

完整JPEG传输首次实测：16帧在22968ms内全部校验完成，GPU任务947.593ms；raw与渲染JPEG哈希和公开计算测试一致。128帧在POST请求体上传的TLS写入阶段超过既定截止，收到0帧、未取得任务事件号；该次不是GPU/SSE计算失败，不定位为平台根因。报告保留 `complete:false` 和已完成16帧结果。未重试、延长预算或替换请求内容；尚未证明128帧端到端加速。
