# 红绿灯检测与灯色判断

本文档说明两部分工作：

1. **已接入后端**：提高红绿灯召回（红绿灯单独阈值 + 画面中央偏上 ROI 二次检测）和"正前方灯"标记。
2. **离线实验，尚未接入后端**：判断人行灯红 / 绿的裁剪图分类器，附接入接口约定，供整合时使用。

定量结果全部来自公开数据集 **ImVisible / PTL（上海）**。组内新加坡自拍照片经聊天软件压缩、夜间模糊，
只用于排查问题，不作为定量结论。

## 1. 已接入后端的改动

| 改动 | 位置 | 说明 |
|---|---|---|
| 红绿灯单独置信度阈值 | `core/detector.py` 的 `CLASS_CONF` | `traffic light` 为 0.12，其他类别仍为 0.25。不开 ROI 也生效 |
| 输入上限 1280 | `core/detector.py` 的 `MAX_INPUT_EDGE` | 服务端接受最长边不超过 1280 的 JPEG。**手机端仍发送 640**（`frontend/camera.js`、`live-detection-client.js`、`hf_video_relay.py` 未改） |
| ROI 二次检测 | `core/detector.py` 的 `roi_crop`；`realtime/detector.py` | 画面水平中间 40%、竖直上方 55% 按原分辨率裁出，letterbox 到 640 后用同一个 CUDA Graph 再推理一次（batch 仍为 1，顺序两次），**只保留其中的红绿灯框** |
| 合并 | `core/detector.py` 的 `merge_boxes` | 只在红绿灯框之间做 class-aware NMS（IoU 0.5），其他类别原样保留、顺序不变 |
| 正前方灯标记 | `core/detector.py` 的 `mark_front_light` | 在竖直的红绿灯框（高 > 宽）中，取离画面水平中线最近的一个，标 `front_light: true`，其余标 `false`，不删框 |
| 离线评估开关 | `model_tests/run_depth.py` | `--roi`、`--max-edge`（默认 640，行为与原来一致） |

metadata 新增字段：`class_confidence_thresholds`；开 ROI 时还有 `roi`、`roi_ms`。
`inference_ms` 和 `forward_event_ms` 为两次推理之和。

用法（GPU 机器，`backend/` 目录下）：

```bash
python -X utf8 -m model_tests.run_depth --input <图片目录> --output <新的输出目录> --roi --max-edge 1280 --label roi
```

### 检测结果（ImVisible 测试集 739 张，以图为单位）

ImVisible 没有灯的框，因此"检出"指：主灯存在的图里，至少检出一个红绿灯框。车行灯被检出也算，所以结果偏乐观。

| 配置 | 有灯图检出率 | 无灯图误报 |
|---|---|---|
| 红绿灯阈值 0.12 | 0.84 | 6/54 = 0.11 |
| 阈值 0.12 + ROI | **0.93** | 9/54 = 0.17 |

延迟（Colab T4，57 张静态图平均）：检测 23 → 53 ms，端到端 79 → 114 ms（开 ROI）。

## 2. 人行灯颜色分类器（离线实验）

### 三种方法对比（ImVisible 测试集，以图为单位）

| 方法 | 结果 | 结论 |
|---|---|---|
| HSV 颜色规则（基线） | 判对率 0.55–0.62；红灯说成绿 3–14/226；约 27% 判"不确定" | 太粗糙 |
| 整图分类器（MobileNetV3-Large，576×432） | 4 类准确率 0.951；τ=0.5 时红灯说成绿 2/226，绿灯识别率 0.97 | **不采用**：检查误判样本发现，红灯时只要有人在过马路就判"绿"（shortcut learning） |
| **灯裁剪图分类器（MobileNetV3-Small，96×96）** | τ=0.95 时红灯说成绿 **4/226（1.8%）**，绿灯说出"绿" 0.61 | **采用**：只看灯本身，切断"看人猜灯" |

### 裁剪图分类器的做法

1. 用现有检测器（阈值 0.12 + ROI）在 ImVisible 训练、验证图上找红绿灯框。
2. 挑灯：选离斑马线中线远端最近、且在其上方的框（ImVisible 标注了斑马线中线）。
3. 自动打标签：标签取该图的主灯状态（红 / 绿 / 黑屏）；HSV 明显相反的样本丢弃（多半挑到了车行灯）。
   共得到训练 2,788 张、验证 694 张裁剪图，不需要人工画框。
4. ImageNet 预训练的 MobileNetV3-Small 微调 25 轮；数据增强**不调色相**；类别加权。
5. 安全阈值 τ：只有绿灯概率 ≥ τ 才输出"绿"，否则红灯概率最高输出"红"，其余输出"unknown"。

### 阈值取舍（ImVisible 测试集）

| τ | 0.5 | 0.7 | 0.8 | 0.9 | **0.95** |
|---|---|---|---|---|---|
| 红灯说成绿 | 25/226 (11%) | 19/226 (8.4%) | 15/226 (6.6%) | 6/226 (2.7%) | **4/226 (1.8%)** |
| 绿灯说出"绿" | 0.74 | 0.69 | 0.67 | 0.64 | **0.61** |

建议 τ = 0.95（不做多帧确认时）。

另试过"只保留 HSV 同意标签的干净样本"重训（v2）：验证集看似完美（红说成绿 0/169），
但验证集用同一规则清洗、难度被筛低（循环验证），模型过度自信，测试集反而更差（8/226）。**不采用。**

### 延迟（Kaggle Tesla T4，200 次中位数 / p95，单位 ms）

| 每帧灯数 | 裁剪 + 预处理（CPU） | 推理（GPU） | 合计 FP32 | 合计 FP16 |
|---|---|---|---|---|
| 1 | 0.45 / 0.54 | 5.26 / 6.15 | **5.7 / 6.7** | 7.5 / 8.6 |
| 3 | 1.35 / 1.76 | 5.42 / 7.43 | **6.8 / 9.3** | 8.4 / 9.0 |
| 5 | 2.19 / 2.53 | 5.49 / 6.18 | **7.7 / 8.6** | 9.7 / 13.2 |

端到端约慢 5–7%。GPU 时间几乎不随灯数变化，主要是 kernel 启动开销；用 FP32 即可。
若需进一步降低，可参照现有 YOLO 的 CUDA Graph 写法（未实测）。

## 3. 接入接口约定（给整合的同学）

参照 `realtime/depth.py` 中 `DepthEstimator` 的接入方式，作为可选的独立模型：

```
class LightStateClassifier:
    __init__(weights_path, expected_sha256)   # 启动时加载一次，校验哈希
    activate()                                # 在 GPU 分配后调用一次
    predict(image, boxes) -> boxes            # image: 原始 RGB；boxes: 已检出的框
```

- 只处理 `class_name == "traffic light"` 的框：框四周各扩 25%，letterbox 到 96×96，ImageNet 归一化。
- 在这些框上新增 `light_state`（`red` / `green` / `unknown`）和 `light_prob`。
- metadata 新增 `light_model`、`light_ms`。
- 规则：`p_green ≥ τ (0.95)` → `green`；否则 `argmax == red` → `red`；否则 `unknown`。
- 权重：`light_crop.pt`（6.2 MB）。仓库按 `.gitignore` 不提交 `models/*.pt`，需单独分发；
  放入 `backend/models/` 并参照 `yolo26n.json` 增加 SHA256 清单。
- 在分类器训练好之前，可以先接一个总是返回 `unknown` 的空实现，不影响其他模块。

## 4. 局限（汇报时必须说明）

1. **ImVisible 上挑灯用了人工标注的斑马线中线**，实际运行时没有，所以颜色分类结果是偏乐观的上限。
   运行时目前用 `front_light`（离中线最近的竖直框），选对率明显更低。
2. 检测只能以图为单位评估（无灯框标注），偏乐观。
3. 只有单帧结果，没有视频多帧确认。
4. 只在上海数据上验证；人行灯样式随城市不同。
5. 延迟在 Kaggle / Colab T4 上测得，部署 GPU 不同需重测。

系统定位为"前方有灯 / 红灯请等待"的安全提醒，**不作为过街决策依据**。

## 5. 复现脚本

均在 `tools/traffic_light/`，为 Colab / Kaggle 上运行的离线实验脚本（使用 `/content` 路径），不属于运行时代码。

| 脚本 | 作用 |
|---|---|
| `imvisible_eval.py` | 下载 ImVisible，跑检测，统计以图为单位的检出率和 HSV 颜色基线 |
| `train_light_cls.py` / `light_cls_check.py` | 整图分类器训练与误判检查（已弃用的对照方案） |
| `train_light_crop.py` | 裁剪图分类器：自动生成裁剪图、训练、校准 τ、测试 |
| `train_light_crop_v2.py` | τ 取舍曲线 + 干净数据重训对照 |
| `light_crop_bench.py` | 分类器延迟测试 |
| `eval_detection.py` / `eval_front_light.py` | 自拍照片的检测评估（近距离口径 `--near`、正前方人行灯口径） |

数据：ImVisible 标注来自 [samuelyu2002/ImVisible](https://github.com/samuelyu2002/ImVisible)，
图片为其 Google Drive 上的 768×576 版本（仓库为 MIT 许可，图片许可未单独说明）。
