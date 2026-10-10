# 本地深度测试样例

`bus.jpg` 是 Ultralytics 官方公开示例图，来源：
[bus.jpg](https://ultralytics.com/images/bus.jpg)。原始字节保留，
SHA256：`c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63`。
来源归属 Ultralytics 官方示例；不据此重新声明图片版权或批量数据集许可。

该图片没有检测/深度真值标注，仅用于验证双模型推理、框与距离标签和结果输出。
它不是训练集、准确率评估集或真实手机视频。程序按既有640最大边/JPEG80契约处理原图，
不修改这里保存的原始图片。

从 `backend/model_tests` 执行：

```powershell
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-run-3
```

输出目录必须是新目录。公开数据集选择与模型对照边界见
[DATASETS.md](../DATASETS.md)，运行说明见 [README.md](../README.md)，
融合实现见 [DEPTH.md](../../DEPTH.md)。
