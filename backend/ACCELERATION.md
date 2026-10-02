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

以上记录来自当时的独立实验，数值、数据划分与评估口径保留。未上线的低精度/OpenVINO实验执行工具已移除，原实现可从 Git 历史查阅；当前运行只保留已验证的 FP32 CPU 流程和 HF ZeroGPU。原始 reports 继续保留于 benchmarks，以便溯源。新的同类框一致性验证使用 `tools.benchmark_space` 和 `tools.compare_detections`，不是重新执行旧后端对照。
