# ZeroGPU 当前视频路径

手机入口为 https://47.239.50.129/prs/live/ ，处理模式只有 Echo 与 Hugging Face ZeroGPU。
原 `/prs/` 转到此页面。CPU、单帧 HF、分段视频、前端绘框及旧20秒会话均已删除。

HF 只保留 `detect_live` 分配 API 和 `/prs_live/ws` 二进制流。浏览器通过同源
`/ws/detect/hf/` 连接 PRS，PRS 使用服务端私有身份连接 HF。首帧单独完成，之后默认12FPS
采样上限、最多5个在途作业；按序验证和显示完整标注 JPEG。一次 GPU 分配最多55秒，
主动停止、断开、到时或输入闲置会释放；结束后手动开始，不自动重新申请。

开始前同时要求免费 GPU 秒数≥90、免费调用次数>0；不足或无法确认时拒绝。
本项目只用 ZeroGPU 每日免费额度，不申请独占或付费硬件。推理条件保持原始
YOLO26n/640/confidence0.25/JPEG0.8。计算精度与后端由必需的
`models/inference-plan.json` 声明；同版本三个独立进程都必须完成5000张COCO验证，
AP相对公平FP32 baseline下降不超过0.5个百分点。公开结果数组仍为FP32。
2026-10-05按纯推理目标选择v8重复确认的FP16/融合/channels-last/default编译/Graph组合，
HF提交eb063ebc包含缓存复用及同PID前端状态重置。完整COCO证据仍为T4上的三个5000图
进程；v11/v12是缓存实现诊断，HF只跑两次8帧短调用。历史AOT与公网长测保留，
本次不以公网FPS评选推理方法；当前部署和验证记录见 [PHONE-LIVE.md](PHONE-LIVE.md)。

开发运行的 `HF_INFERENCE_TOKEN` 配在私有 `backend/.env`；部署用写权限 `HF_TOKEN`
配在私有 `backend/.env.hf`。两者排除 Git，服务端私有配置权限0600；浏览器没有 token。
运行写入 `server_info.txt` 的 SSH 信息也不进入源码、日志或公开 Space。

`python -m tools.deploy_space` 生成仅当前路径的来源白名单；显式 `--publish` 将来源更新
与旧文件删除放在一个带父提交校验的 HF commit 内。部署不变更硬件；编译方案在
HF启动时进行单独的真实GPU编译。缓存JIT保留官方artifacts和Inductor/Triton目录，
每次分配先重置前端状态（HF可复用同PID），严格核对缓存/硬件/运行时/配置，要求FX
命中并捕获本次CUDA Graph；不直接迁移T4二进制，不在失败后切换后端或隐式重新编译。
公网检测结果、长尾限制和测试范围见 [PHONE-LIVE.md](PHONE-LIVE.md)。
