# Hugging Face ZeroGPU 部署

目标 Space：[Heropig114514/prs-yolo26](https://huggingface.co/spaces/Heropig114514/prs-yolo26)。Space 已部署并实际返回标注图。手机页面新增显式 Hugging Face ZeroGPU 模式，云服务器通过 HTTPS 调用该 Space；CPU 和 laptop TensorRT 模式保持原有选择与参数。

代码位于 `zerogpu/`，与 Django 和 laptop 环境隔离。为适配 ZeroGPU，使用 PyTorch2.8.0 + Gradio6.29.0；保持原模型 SHA256、FP32、640 输入、0.25 阈值、300 候选和现有 Pillow 预处理/标注。导出时选用的 one-to-one 检测头被显式恢复，不使用 Ultralytics 默认图像预处理或额外 NMS。原始权重在启动时从固定官方 URL 下载一次，验证哈希后才能加载。启动失败不会换模型或转 CPU。

在项目根目录生成上传包：

```powershell
python -X utf8 backend/tools/deploy_space.py
```

只生成 `backend/.space-bundle/` 的明确文件清单；不会上传整个私人仓库。包内有 root `app.py`、README、Python/容器依赖、推理包、共享 `core.detector`、模型 manifest 和公开 bus 样例 JSON。已填 `.env`、SSH 配置、密钥和手机图像不在清单内。

已在本机通过 Hugging Face CLI 登录且持有对目标 Space 的写权限时，可执行：

```powershell
python -X utf8 backend/tools/deploy_space.py --publish
```

发布器使用现有 `HF_TOKEN` 环境变量或标准 `~/.cache/huggingface/token`，也可用 `HF_TOKEN_PATH` 指定私有 token 文件。一次提交全部允许文件，不生成 token、不升级收费硬件、不自动重试。不要把 token 放进代码、命令参数、公共 Variables 或聊天正文。浏览器已登录也可以上传包中文件；最终应为同一套源文件，`core/detector.py` 不应手动维护第二份。

`/status` API 只表示权重初始化，不代表 GPU 可用。`/detect` JSON 协议和限制见 [Space 说明](zerogpu/README.md)。测试按钮只使用公开样例；API 使用 base64 JSON，应用不把上传/返回的相机图像写入磁盘。发送图像会将其交给 Hugging Face 处理。

选择 ZeroGPU 后先点击“测试示例识别”，再授权摄像头。链路为手机 → PRS 云服务器 → Hugging Face ZeroGPU → PRS → 标注 JPEG 屏幕。只有此模式使用用户批准的60秒客户端预算：云端 HTTPS/SSE 共用55秒截止时间，Nginx 仅 `/prs/vision/hf/` 读等待65秒；其余 Nginx 路径仍30秒，CPU/laptop 客户端仍10秒，laptop 转发仍8秒。串行发送、不自动重试、不切换计算后端，配额耗尽明确停止。

浏览器登录不等于服务器 API 身份。服务端可设置 `HF_INFERENCE_TOKEN`，没有设置时配置接口明确返回 `authentication: anonymous`。本机私有 `backend/.env.hf` 可填 `HF_INFERENCE_TOKEN=hf_...`，排除 Git；部署时只进入服务器 root 0600 的 `/etc/prs-camera/app.env`。不要传给前端或 Space 公共 Variables。token 用于推理，无需仓库写权限；若 token 无效，请修正配置，不自动改匿名调用。账号配额、排队和共享 GPU 服务不能保证连续视频帧率。

2026-10-02 真实 Space bus 验证返回5个框，JPEG SHA256与既有 ONNX 基线一致；首次端到端约33.7秒，GPU阶段约715ms，其余包含网络、排队、分配和处理。报告见 `benchmarks/2026-10-02/zerogpu-remote.json`。随后4张验证图测试在第3张遇到真实匿名 runs limit，完整远端4张一致性验证尚未完成，不能将本地13/13作为远端结果。

本地 GPU smoke test 使用已有 PyTorch2.5.1+cu121，只证明原始权重和共享处理管线工作，不证明 Space 的 PyTorch2.8/Blackwell/CUDA emulation 可用。独立 `.venv-space` 用于本次测试；没有改动 `.venv` 或 `.venv-laptop`。检查结果和真实远端测试记录应分别报告。

2026-10-02 本地结果：bus 样例5个框，标注 JPEG SHA256 与 ONNX 基线一致；4张验证图按已有 `tools.benchmark_laptop.prepared_image` 生成相同 JPEG80 输入，同类 IoU≥0.5 匹配13/13，平均置信度绝对差约3.69e-7，raw预测不是逐位相等。这是小样例一致性检查，不是 COCO mAP。报告见 `benchmarks/2026-10-02/zerogpu-local.json`。

实际远端4张测试可从 backend 执行（在当前进程私有设置 `HF_INFERENCE_TOKEN`，不放命令参数）：

```powershell
python -m tools.benchmark_space --evaluation models/experiments/coco8/images/val --reference benchmarks/2026-10-02/baseline.json --output benchmarks/2026-10-02/zerogpu-remote-validation.json
```

验证工具保留已有输入、IoU0.5与类别匹配方式；逐帧记录进展，出错不重试。远端协议不返回raw预测哈希，报告中逐位一致性为null；完整比较仅在4张全部返回后生成。
