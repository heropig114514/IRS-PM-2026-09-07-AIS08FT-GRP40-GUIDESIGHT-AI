# 数据集存放位置

把自己拍摄的图片或下载的公开数据集放在这里，保留原始目录和真值标注。
例如 `my-photos/`、`coco/val2017/`、`nyu-depth-png/images/val/`。
`street-video/mot17/` 中的4个MOT17测试视频及 `street-video/SOURCES.json` 属于版本管理范围，
包括07/10各自的原始预览和H.264浏览器回放副本。完整数据集、标注、抽帧图和本机运行
报告被Git忽略；需要使用时单独准备，不会自动下载或划分。

MP4回放使用 `/integration/video/`，选择 `*-browser-h264.mp4`；下方图片脚本不直接读取视频。
启动及操作见 [本机MVP说明](../../LOCAL-MVP.md#连续本地视频入口)。

从 `backend/model_tests` 执行：

```powershell
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1
```

请将 `--input` 指向 RGB 图片子目录，避开包含深度、掩码的 PNG 目录。
数量建议、公开下载入口及真值说明见 [DATASETS.md](../DATASETS.md)。
