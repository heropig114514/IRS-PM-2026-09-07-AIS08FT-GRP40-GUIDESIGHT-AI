# Laptop GPU 推理与统一启动

完整链路：**手机 → 云服务器 → SSH 隧道 → laptop GPU → 同一 SSH 隧道 → 云服务器 → 手机标注图**。手机始终调用云端 HTTPS，laptop 是云服务器调用的计算节点。

## 现有机器：一条指令启动

在项目根目录运行：

```powershell
python backend/start_laptop.py
```

启动器自动使用 `backend/.venv-laptop`，读取私有 `backend/.env.laptop`，检测兼容的 NVIDIA GPU，校验固定 ONNX，选择本机引擎缓存，然后启动本地推理服务和 SSH 反向通道。首次构建可能需数分钟；后续直接校验并复用缓存。SSH 密码交互输入，不保存。可配置私钥认证，带密码的私钥仍交互输入密码。

看到 `Laptop node ready: GPU worker + SSH tunnel active` 即两者已启动；配置了公网 URL 时，还会先进行一次云端真实就绪检查。终端保持运行；Ctrl+C 同时关闭本地 worker 和该 SSH 连接的云端监听。SSH 断连或 worker 退出会终止组合服务，须手动再次启动，不自动重连/重试/切换 CPU。不设置开机启动或修改电源计划。

当前机器的私有配置已填入现有 PRS 服务器。访问 [PRS](https://47.239.50.129/prs/)，选择 **Laptop GPU · TensorRT · 80 类**，点击“测试示例识别”；手机随后手动启用摄像头。

## 其他人部署

1. 安装 Python **3.12** 和兼容 NVIDIA 驱动；Windows/Linux x64 可用。无需另装系统 CUDA Toolkit。
2. 在项目根目录运行一次：

```powershell
python backend/start_laptop.py --setup
```

该操作创建或使用项目专属 `.venv-laptop`，按检测到的 GPU 安装固定 TensorRT/CUDA runtime 依赖，并执行 pip check，不修改全局 Python、系统 CUDA 或驱动。

3. 复制 `backend/.env.laptop.example` 为 `backend/.env.laptop`（Windows 可用 `Copy-Item`，Linux 可用 `cp`），配置：

| 配置 | 含义 |
|---|---|
| `DJANGO_SECRET_KEY` | 独立随机私密值，至少32字符 |
| `LAPTOP_INFERENCE_TOKEN` | 与云端本项目环境中的 token 相同，至少32字符 |
| `LAPTOP_SSH_HOST / USER / PORT` | 自己的云服务器和 SSH 账号；SSH 端口默认22 |
| `LAPTOP_SSH_KNOWN_HOSTS` | 已核验主机密钥的 known_hosts，默认 `~/.ssh/known_hosts` |
| `LAPTOP_SSH_KEY_PATH` | 可选私钥绝对路径；留空时交互输入 SSH 密码 |
| `LAPTOP_WORKER_PORT` | 本机空闲 loopback 端口，默认18767 |
| `LAPTOP_REMOTE_PORT` | 云端空闲 loopback 端口，默认18767；须等于云端 `LAPTOP_INFERENCE_PORT` |
| `LAPTOP_GPU_INDEX` | 留空自动选择首个兼容显卡；可指定物理编号 |
| `LAPTOP_ONNX_PATH` | 固定 ONNX 的本地路径，默认相对于backend的 `models/yolo26n.onnx` |
| `LAPTOP_SERVER_ONNX_PATH` | 可选云端 ONNX 绝对路径；本地缺失时通过已认证 SSH/SFTP 获取并校验 SHA256 |
| `LAPTOP_PUBLIC_URL` | 可选云端页面 HTTPS URL，包含项目路径，如 `https://your-domain/prs/`；启动后验证就绪 |

首次连接前通过 SSH 核验管理员提供的主机密钥指纹并保存信任记录。启动器拒绝未知或变化的主机密钥。Linux 将私有环境文件设为0600；Windows限制到本用户/SYSTEM/Administrators。环境文件、token、密码、私钥和本机构建缓存均不得提交或发给前端。

模型二进制不在Git中：使用与 `models/yolo26n.json` 中 SHA256 相同的原 ONNX，或配置云端该文件位置。不会自行导出不同权重、改变模型或使用其他下载来源。

4. 云端已部署本项目后，在本项目环境设置相同 token 和推理端口，仅重启本项目服务。随后运行同一条 `python backend/start_laptop.py`。另一台 laptop 使用相同云端监听端口时，须先停止当前节点；这是一条单节点通道，不包含多节点负载均衡。

多显卡可在安装和启动时均显式选择：

```powershell
python backend/start_laptop.py --setup --gpu-index 1
python backend/start_laptop.py --gpu-index 1
```

若通过环境文件指定非默认显卡，首次安装也须用相同 `--gpu-index`。缺少显卡、驱动不兼容、版本不匹配、端口占用、私钥/认证失败或缓存损坏均明确停止。不会关闭其他应用或自动升级驱动；检查提示后手动处理。

## 显卡适配与隔离

| NVIDIA compute capability | 自动安装的固定依赖 | 驱动报告 CUDA 能力要求 |
|---|---|---|
| 7.5 / 8.0 / 8.6 / 8.9 / 9.0（包含常见 RTX20/30/40） | TensorRT10.7.0.post1 + runtime12.6.77 | ≥12.6 |
| 10.0 / 12.0（支持的 Blackwell，包含 RTX50） | TensorRT10.8.0.43 + runtime12.8.90 | ≥12.8 |

这是本项目明确的兼容范围；“有 NVIDIA 显卡”不等于所有旧显卡均支持。驱动要求采用保守检查，读取 `nvidia-smi` 的能力值而非系统 `nvcc`。依据：[TensorRT10.7支持矩阵](https://docs.nvidia.com/deeplearning/tensorrt/archives/tensorrt-1070/pdf/TensorRT-Support-Matrix-Guide.pdf)、[TensorRT10.8/Blackwell说明](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/getting-started/release-notes-10/10.8.0.html)。

引擎位于被Git忽略的 `backend/models/tensorrt/`，缓存身份包含GPU UUID/型号/SM、驱动、OS/架构、TensorRT版本、ONNX SHA256、FP32/TF32关闭/256MiB workspace。不同GPU/驱动/OS会使用不同缓存，不能把别人的engine直接拿来运行。CUDA DLL/SO从隔离环境的runtime包定位，不再硬编码Windows安装目录。

保持YOLO26n、80类、640输入、0.25阈值、JPEG80、1MiB最大帧和200ms间隔；固定FP32、TF32关闭，不启用FP16/INT8。不同FP32实现不能保证逐位相等。256MiB仅是构建tactic workspace，不是整机显存上限；其他GPU程序会争用资源。

两端推理端口只监听127.0.0.1，本地仅主动连接配置的SSH端口。启动器先保留本机端口防止重复启动，再检查云端空闲端口，worker就绪后才激活反向监听，并检查实际绑定。最多4个转发通道，推理仍只允许一个在途请求；不排队。无需家庭端口映射，不修改GatewayPorts、防火墙、Nginx或其他应用。

云端既有8秒上游预算、响应校验、20%CPU/256MiB内存/32tasks保持不变。手机仍通过同源接口接收经云端校验的JPEG与检测元数据。

## 验证与限制

Windows RTX4060 Laptop/驱动566.36已实际验证：`--setup` 安装检查通过；同一命令完成显卡检测、首次引擎构建、worker、SSH监听及公网就绪；Ctrl+C后本地端口释放，公网laptop接口503；再次用同一命令启动，云端就绪并返回公交车图5个标注目标，JPEG SHA256与原基线相同。

既有离线速度/4张样例质量结果见 [TensorRT结果](benchmarks/2026-10-02/laptop-tensorrt.json)，既有网络实测见 [公网结果](benchmarks/2026-10-02/laptop-public.json)。GPU背景负载会影响时间；样例不是完整COCO精度，也不代表手机持续FPS。Linux、Blackwell及其他显卡仅完成配置/策略/库路径的模拟测试，未在这些硬件上实际跑模型；手机摄像头权限、续航和长期帧率仍需真机验证。

后端检查从backend执行 `python manage.py check`、迁移dry-run、完整test、`ruff check .`，另运行前端Node测试及pip check。新增测试模拟GPU/SSH/Uvicorn，覆盖配置、缓存、固定模型校验、精确loopback绑定、断连与异常清理；不能代替上述真实链路验证。当前无自动注释检查器，变更注释和索引人工核对，与实现一同提交。
