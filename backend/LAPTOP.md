# Laptop GPU 推理服务

2026-10-02，用户选择只配置 YOLO + TensorRT。机器为 Windows、RTX 4060 Laptop GPU 8 GiB、驱动566.36、约31GiB系统内存，WSL2 Ubuntu24.04可访问GPU。本次使用 Windows原生TensorRT10.7.0.post1 / CUDA runtime12.6.77，隔离安装在 `backend/.venv-laptop`。未升级驱动、系统CUDA11.8或全局Python。vLLM面向大语言模型，本次不安装、不下载其他模型。

## 路径与边界

完整请求/响应链路：**手机 → 云服务器 → SSH隧道 → laptop GPU → SSH隧道 → 云服务器 → 手机屏幕上的标注图**。

手机通过HTTPS调用云端PRS `/prs/vision/laptop/frame/`。云服务器将JPEG请求发往自己的`127.0.0.1:18767`，SSH反向端口转发把它送达laptop的`127.0.0.1:18767`推理服务。Laptop GPU执行TensorRT并生成标注JPEG，响应通过同一条双向SSH隧道返回云服务器；云服务器读取并校验JPEG、尺寸和检测元数据，再通过原手机HTTPS请求返回图像。云服务器始终是手机的统一入口和响应方，laptop作为其调用的推理服务器/计算节点。

Laptop主动连接SSH22，不需要家庭端口映射。两端推理端口只监听loopback，启动时验证实际服务器绑定，不修改GatewayPorts、防火墙或Nginx。私有接口另外使用随机bearer token；SSH口令交互输入，不写文件/参数。校验已有`~/.ssh/known_hosts`，主机密钥不匹配直接拒绝。

前端新增明确的 **Laptop GPU · TensorRT · 80 类** 模式，其他模式仍由用户单独选择。Laptop断连/休眠/模型故障时该模式报错并停止，不自动换CPU、重连、重放或重试。不修改电源计划，不设置开机启动。保持推理进程和隧道才能接受请求。

固定YOLO ONNX权重、640输入、0.25阈值、JPEG80、1MiB最大帧和200ms间隔均不变。TensorRT使用FP32，关闭TF32，不启用FP16/INT8。不同FP32算子实现仍可能产生数值差异，不能声称逐位相等；`.engine`不能直接复制到其他GPU/不同TensorRT版本。

服务器/worker各允许一个在途调用，拒绝排队，复用的GPU张量和执行上下文由该边界串行保护。上游8秒socket超时/正文截止时间，图像最多1MiB、配置/错误正文4KiB，拒绝重定向，校验模型/精度/计数/时长/JPEG尺寸。连接建立和响应头仍属于socket超时边界，不声称严格的全请求墙钟硬截止。客户端维持10秒超时，公网传输影响最终帧率。

TensorRT构建workspace限制256MiB，只限制tactic workspace，**不是GPU/电脑总内存硬上限**；编译另需主机内存和显存。其他游戏/CUDA应用会竞争GPU。服务器原20%CPU/256MiB内存/32tasks限额保持不变，仅修改本项目路由/私有环境，不修改其他应用。

## 安装与启动

项目根目录：

```powershell
python -m venv backend/.venv-laptop
backend/.venv-laptop/Scripts/python.exe -X utf8 -m pip install --index-url https://pypi.org/simple --extra-index-url https://pypi.nvidia.com -r backend/requirements-laptop.txt
cd backend
.venv-laptop/Scripts/python.exe -X utf8 -u -m tools.build_tensorrt --onnx models/yolo26n.onnx --engine models/yolo26n-trt-fp32.engine
```

构建写入engine和同名JSON（源/引擎SHA256、版本、形状、精度和workspace），失败立即停止；运行时校验。源码/公开清单可提交，engine/venv/私有环境忽略。

`backend/.env.laptop` 单独创建，含`DJANGO_SECRET_KEY`、`LAPTOP_INFERENCE_TOKEN`及绝对`LAPTOP_ENGINE_PATH`，Windows ACL限定本用户/SYSTEM/Administrators，不打印/提交。服务器`/etc/prs-camera/app.env`中配置同一token，保持root0600。Token不能进入前端/URL/日志。

两个终端，工作目录均为backend：

```powershell
# 终端一：GPU worker（先加载模型再监听）
.venv-laptop/Scripts/python.exe -X utf8 -u -m tools.run_laptop
# 终端二：隧道（交互输入现有SSH口令）
.venv-laptop/Scripts/python.exe -X utf8 -u -m tools.laptop_tunnel
```

打开 `https://47.239.50.129/prs/` 选择Laptop模式，点击“测试示例识别”。成功后手机选择同一模式、手动开启摄像头。各终端Ctrl+C停止；断连后须手动启动。Agent启动的后台worker先按记录的PID停止，避免重复端口。

## 验证

本次公开样例实测（其他GPU应用运行中，Python perf_counter高精度计时）：平均GPU推理含拷贝/同步 **3.82ms**，平均完整本地解码/预处理/推理/绘制/编码 **8.77ms**。公交车图5个目标，标注JPEG的SHA256与原ORT基线相同。4张val图13/13参考框匹配，置信度平均绝对差约4.03e-7，原始张量不是逐位相同。见 [原始结果](benchmarks/2026-10-02/laptop-tensorrt.json)。不包含网络、手机采集或200ms间隔，不代表手机FPS或完整COCO精度。

部署后经公网/SSH连续10次样例请求全部成功，平均往返519.9ms、GPU含传输平均39.77ms；浏览器样例5个目标、往返460ms、GPU含传输105ms。连续本地测试与独立HTTP请求的时间明显不同，包含线程/执行环境及GPU背景负载差异，尚未逐项归因，不应以3.82ms保证单请求时延。原应用登录页、地图、健康、CPU模式及未授权worker401通过。手动停止隧道后laptop返回503且CPU仍ready，手动重启后TensorRT ready。见 [公网验证](benchmarks/2026-10-02/laptop-public.json)。

52项后端、27项前端测试通过，Django配置/迁移dry-run/Ruff和CI通过；实现与注释/声明索引在同一代码提交。当前Windows后台worker和交互隧道已启动，重启机器后按上方两个命令手动启动，不保存SSH口令。仅重启PRS，其他服务PID与Nginx/systemd配置哈希在部署前后相同。第一次紧随restart的探测曾返回502，随后确认服务启动完成并通过真实请求；部署没有配置自动重试或回退。

从backend运行 Django check、迁移dry-run、test和Ruff，前端Node tests。注释与声明索引人工检查，当前无自动docs检查器。新增测试中的模型/HTTP替身不代表真实GPU/SSH验证。

```powershell
.venv-laptop/Scripts/python.exe -X utf8 -u -m tools.benchmark_laptop --engine models/yolo26n-trt-fp32.engine --image ../frontend/detection-sample.jpg --evaluation models/experiments/coco8/images/val --reference benchmarks/2026-10-02/baseline.json --output benchmarks/2026-10-02/laptop-tensorrt.json
```

一帧预热、10次公交车图、4张COCO8 val按既有JPEG80/640准备。对照原ORT FP32预测，同类别IoU≥0.5；参考不是标注真值，不是mAP。GPU时间含传输/同步，旧CPU推理时间仅ORT，口径不同。总耗时也跨机器/资源预算，不能单独归因TensorRT。真实手机权限、续航与持续帧率需另测。

依据：[TensorRT安装](https://docs.nvidia.com/deeplearning/tensorrt/latest/installing-tensorrt/install-pip.html)、[TensorRT10.7说明](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/getting-started/release-notes-10/10.7.0.html)、[vLLM/WSL](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)。实际安装/编译优先于泛化兼容推断。
