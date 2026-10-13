# 测试输出

每次指定一个新的子目录，如 `outputs/bus-run-3` 或 `outputs/photos-run-1`。
里面保存 `run.json`、`results.jsonl` 和 `annotations/`；生成结果不会提交 Git。
此前 `backend/test-results/depth/` 的所有文件已原样迁入本目录，包括 `bus-run-1`、`bus-run-2`。
历史结果中的绝对输入路径按运行当时保留，新运行记录新的示例路径。

`run.json` 的 `complete` 表示成功；`failed` 保留错误码和部分结果。
历史中断的 `bus-run-1` 仍是原始 `running` 状态和空结果，未改写成成功。
已有目录即使失败或中断也不会自动覆盖；请换一个运行名。
详细解释见 [README.md](../README.md)。
