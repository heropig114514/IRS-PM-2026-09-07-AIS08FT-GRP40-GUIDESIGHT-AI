# YOLO26n 模型工件

首版使用 Ultralytics 官方 YOLO26n COCO 80 类预训练权重，无需额外训练；这不属于任意类别的零样本识别。台阶、坑洞及道路安全判断不由这 80 类检测器保证。

`yolo26n.json` 跟踪模型、类别顺序、原始权重来源、构建版本和 SHA256。权重与 ONNX 文件不提交 Git。官方来源：[YOLO26](https://docs.ultralytics.com/models/yolo26/)、[导出说明](https://docs.ultralytics.com/modes/export/)。该模型及 Ultralytics 构建工具遵循其 AGPL-3.0 条款；这里不改变本仓库其他文件的授权。

在独立构建虚拟环境安装 `requirements-vision-build.txt`，从 backend 运行：

```powershell
python tools/export_yolo26.py --output models
```

脚本从清单中的官方 HTTPS 发布下载原始权重，固定 CPU FP32、640、batch=1、opset=17、静态形状、`nms=False`、`simplify=False`、max_det=300。使用 one-to-one 端到端输出 `[1,300,6]`，服务端不执行额外 NMS。重新导出可能改变工件哈希，必须把新工件和新清单一起发布；服务端不自动下载、转换或替换模型。

摄像头可显式选择 HF、本地 PRF2 或常驻 resident-v1 服务；这些 GPU 入口使用经 SHA256 校验的
PyTorch YOLO26n 权重。CPU ONNX 服务和 `YOLO_MODEL_PATH` 配置已删除。
上方导出脚本仅用于离线工件复现，不构成可选择的处理模式。
输入为 RGB 居中 letterbox、Pillow bilinear、114填充、FP32/255、640、阈值0.25，原尺寸
标注 JPEG80。GPU任务内复用 CUDA Graph；不会自动更换权重或切换后端。

HF 的 `inference-plan.json` 是必需的部署工件，记录实际计算精度、融合/缓冲/Graph/AOT/JIT配置、
同一Notebook版本的源报告SHA与三个完整5000图COCO结果；每个AP降幅须≤0.5个百分点。
输入归一化保持FP32，进入GPU时按明确配置转换，返回数组保持FP32。计划读取/配置/质量
检查失败会中止启动，不使用默认方案。AOT在HF实际GPU启动编译，保留原/兼容包及每个
共享库SHA。缓存JIT则保留官方compiler artifacts和Inductor/Triton目录；新分配要求FX命中，
禁止后端新代码生成及后续重编译。两者均核对硬件/版本/配置；Kaggle二进制不跨GPU发布。

本地默认双模型使用 `yolo26n.pt` 和 `yolo26n-depth.pt`，检测/深度 FP32；本地模型清单、
权重指纹、依赖和显式 A/B/C 配置见 [本地服务](../LOCAL-MVP.md)。常驻优化服务及实验入口
见 [常驻服务](../RESIDENT.md)。本地入场检查不复用 HF 的质量准入记录，也不代表深度精度已验证。

`frontend/tools/fixtures/detection-sample.jpg` 为 Ultralytics8.4.171 包的 bus.jpg 480×640 JPEG80
版本，只在开发浏览器测试中使用，不再作为公开部署资源；原图片字节和来源许可保留。
