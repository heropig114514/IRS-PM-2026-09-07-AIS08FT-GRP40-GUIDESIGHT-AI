# 摄像头检测契约

入口为部署站点的 `/prs/`。选择“YOLO26 检测 · 80 类”，再点击开启摄像头并允许手机浏览器权限；服务端标注框、英文类别和置信度直接绘制在返回 JPEG 中。原样回传仍是默认选项，既有 WebSocket 字节一致契约保持不变。“测试示例识别”只提交公开公交车样例，不申请摄像头。

请求采用同源 HTTP：

| 方法 / 后端路径 | 输入 | 输出 |
| --- | --- | --- |
| GET `/vision/config/` | 无图像 | ready、model=yolo26n、classes=80、confidence_threshold=0.25 |
| POST `/vision/frame/` | Origin 与 Host 同源，Content-Type=image/jpeg，JPEG 正文 | JPEG；X-Detection-Count、X-Inference-Ms、X-Model、X-Request-ID |
| GET `/vision/laptop/config/` | 无图像 | 同一模型/类别/阈值，加 backend=TensorRT、precision=FP32；真实隧道就绪检查 |
| POST `/vision/laptop/frame/` | 同源 JPEG | Laptop GPU 标注 JPEG；同一元数据，加 X-Inference-Backend/Precision |

Laptop 模式明确通过 SSH 私有隧道计算，断连不换 CPU；资源和启动方法见 [LAPTOP.md](LAPTOP.md)。手机、公网服务器和 laptop 都需在线。GPU推理时长包含张量传输及同步，与CPU的ORT执行时间口径不同。

Nginx 的 `/prs` 前缀由既有代理移除。接口不用 Cookie/Authorization，检测客户端拒绝重定向。输入和返回最大 1 MiB，输入最大边 640；页面沿用 JPEG 0.8、串行请求、每次返回后等待 200 ms、10 秒超时。服务端一个非排队模型操作名额，忙碌返回 429；没有隐式回退、自动重试或训练。停止取消客户端请求并丢弃迟到结果，已经开始的服务器推理仍可能运行至完成。

错误返回安全的 code/detail/request_id：403 同源检查失败，415 MIME 错误，400 JPEG 格式/尺寸错误，413 字节边界，429 忙碌，503 配置/模型不可用，502 模型输出/结果边界，500 未预期处理错误。图像仅在内存中处理，不保存到数据库、文件或日志。日志记录请求 ID、目标数、字节数、时长或异常类型，不记录图像正文和密钥。

该原型检测 COCO 80 类，不提供深度、危险等级、追踪或导航决策；未检测到目标不等于道路安全。模型构建和许可信息见 [模型说明](models/README.md)。

2026-10-02 在目标服务器 2 CPU / 3.6 GiB 内存上，以原限额 CPUQuota=20%、MemoryMax=256 MiB、TasksMax=32，加载 Django 后对公开样例串行处理 5 帧：平均 659.7 ms、最大 702.5 ms、进程 RSS 峰值 167.7 MiB，检出公交车及 4 个行人。此数据只是固定样例的资源测试；手机实际帧率、权限和图像识别效果须真机验证。加上传输、采集和 200 ms 间隔，预计约 1 fps，不能据此声称高帧率或通用准确率。

同日进一步优化 FP32 运行流程：有上限的 arena、张量/I/O Binding 复用、直接归一化、批量后处理和字体缓存。新对照测试为 1 次预热 + 10 次测量，平均处理从 625.3 ms 降至 509.7 ms；参数/权重保持不变。它与前面的 5 帧测试协议不同，不混用作直接比较。低精度和后端对照、资源余量及复现见 [ACCELERATION.md](ACCELERATION.md)。
