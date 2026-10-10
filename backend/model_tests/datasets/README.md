# 数据集存放位置

把自己拍摄的图片或下载的公开数据集放在这里，保留原始目录和真值标注。
例如 `my-photos/`、`coco/val2017/`、`nyu-depth-png/images/val/`。
这里的数据内容不提交 Git，也不会自动下载或划分。

从 `backend/model_tests` 执行：

```powershell
..\.venv\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1
```

请将 `--input` 指向 RGB 图片子目录，避开包含深度、掩码的 PNG 目录。
数量建议、公开下载入口及真值说明见 [DATASETS.md](../DATASETS.md)。
