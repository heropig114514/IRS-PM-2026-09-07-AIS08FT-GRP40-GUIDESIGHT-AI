# YOLO26n 模型工件

首版使用 Ultralytics 官方 YOLO26n COCO 80 类预训练权重，无需额外训练；这不属于任意类别的零样本识别。台阶、坑洞及道路安全判断不由这 80 类检测器保证。

`yolo26n.json` 跟踪模型、类别顺序、原始权重来源、构建版本和 SHA256。权重与 ONNX 文件不提交 Git。官方来源：[YOLO26](https://docs.ultralytics.com/models/yolo26/)、[导出说明](https://docs.ultralytics.com/modes/export/)。该模型及 Ultralytics 构建工具遵循其 AGPL-3.0 条款；这里不改变本仓库其他文件的授权。

在独立构建虚拟环境安装 `requirements-vision-build.txt`，从 backend 运行：

```powershell
python tools/export_yolo26.py --output models
```

脚本从清单中的官方 HTTPS 发布下载原始权重，固定 CPU FP32、640、batch=1、opset=17、静态形状、`nms=False`、`simplify=False`、max_det=300。使用 one-to-one 端到端输出 `[1,300,6]`，服务端不执行额外 NMS。重新导出可能改变工件哈希，必须把新工件和新清单一起发布；服务端不自动下载、转换或替换模型。

运行环境仅安装 `requirements-vision.txt`，设置 `YOLO_MODEL_PATH` 为清单匹配的 ONNX 绝对路径。服务器无需 Torch、CUDA 或 Ultralytics。单 CPU 推理线程、固定置信度阈值 0.25，RGB 居中 letterbox、Pillow bilinear、114 填充、FP32 /255；原尺寸返回框并以 JPEG 80 编码。模型初始化失败缓存至进程重启。

`frontend/detection-sample.jpg` 是 Ultralytics 8.4.171 包自带 `assets/bus.jpg` 的 480×640 JPEG 80 版本，仅供公开示例验证，沿用来源许可。页面明确标注“非摄像头”，不将样例当成实拍或准确率评测。
