# 本地测试与模型对比数据

当前使用现成预训练权重即可推理，不需要先训练。
从 `backend` 执行 `python -m model_tests.run_depth` 可运行单图/目录预测、米制距离融合、标注图和 JSONL 导出；
它没有读取真值标注，也没有计算 mAP、深度误差或正式视频 FPS。静态预测入口的示例图片为
`samples/bus.jpg`；连续视频使用下方单独列出的回放文件，不由这个图片脚本直接读取。

## 连续街景视频用于本地联调

当前MVP关注行走者附近的障碍和行人，优先选择相机移动、视角接近人眼高度、包含遮挡和
交错行人的连续街景。MOT17移动相机步行街序列适合回放联调，但不代表完整的步行导航场景。

版本管理范围包括两个官方原始预览、两个H.264回放副本和
[SOURCES.json](datasets/street-video/SOURCES.json)。完整数据集、标注包、抽帧图片和运行
报告单独管理，不包含在这4个视频中；使用额外数据时按官方入口准备。

| 数据集与官方入口 | 视角、数据与适用阶段 | 获取方式与仓库范围 |
| --- | --- | --- |
| [MOT17](https://motchallenge.net/data/MOT17/) | 多目标行人跟踪；07是白天移动步行街，10是夜间移动街景。训练序列有框、轨迹ID及可见度标注；没有米制深度或风险真值。 | 仓库维护4个视频；原始JPG帧集约5.5GB、标注包约10.1MB，均需单独准备。 |
| [VeRLab EPIC2016](https://www.verlab.dcc.ufmg.br/semantic-hyperlapse/epic2016-dataset/) | 头戴GoPro第一视角连续行走。Walking_25p为1080p/30FPS、约6分06秒，单文件915,677,261字节。附带语义检测结果，可补充回放场景，不作为米制深度或风险真值。 | 按官方单文件入口下载，不包含在仓库素材中；数据许可为CC BY-NC 4.0。 |
| [JAAD](https://data.nvision2.eecs.yorku.ca/JAAD_dataset/) | 车辆视角城市道路，346段5–10秒/30FPS视频，带行人框及过街行为标注，适合补充过街场景。 | 官方MP4包约3.1GB，需单独下载；官网声明视频和标注采用MIT许可。 |
| [JRDB](https://jrdb.erc.monash.edu/dataset/) | 接近人体高度的机器人视角，包含RGB、LiDAR及2D/3D行人标注；适合结合标定检查距离和跟踪。全景图与透视相机的对应关系需另外适配。 | 官方旧版下载入口需要登录；图像、点云、标定和时间对应关系需分别准备，不包含在仓库中。 |

EPIC2016指VeRLab的第一视角视频数据集，不是EPIC-KITCHENS。其Walking比例表示论文定义的
语义信息占比，不是障碍物占比；[随包说明](https://www.verlab.dcc.ufmg.br/hyperlapse/downloads/epic2016/README.md)
写明MAT中的框由语义检测器生成，不能把它们说成人工核验的行人跟踪真值。
可直接下载 [Walking_25p原片](https://www.verlab.dcc.ufmg.br/hyperlapse/downloads/epic2016/Source_Videos/Walking_25p.mp4)，
无需下载完整10GB包。单片时长超过现有55秒推理会话；当前不会隐式分段、续接或延长会话。
JRDB的下载形式见 [官方旧版下载页](https://jrdb.erc.monash.edu/old)。

视频来源及许可边界见 [SOURCES.json](datasets/street-video/SOURCES.json)，项目不重新授权
原始视频。样本范围仅包含下表4个测试视频及其来源信息，不包含完整数据集、标注ZIP、
抽帧图片和本机逐帧运行报告。

### MOT17回放素材

4个视频位于 `backend/model_tests/datasets/street-video/mot17/`，合计68,146,915字节。
下表描述MP4文件属性，原始JPG帧集的尺寸另见标注说明。

| 序列与官方原始预览 | 划分/场景 | 实际MP4尺寸/FPS/帧数/时长 | 浏览器可用文件 |
| --- | --- | --- | --- |
| [MOT17-07-FRCNN-raw.mp4](datasets/street-video/mot17/MOT17-07-FRCNN-raw.mp4) | test；白天、移动相机、人眼高度的步行街 | 960×540 / 30 / 500 / 16.666667秒 | [MOT17-07-FRCNN-browser-h264.mp4](datasets/street-video/mot17/MOT17-07-FRCNN-browser-h264.mp4) |
| [MOT17-10-FRCNN-raw.mp4](datasets/street-video/mot17/MOT17-10-FRCNN-raw.mp4) | train；夜间、移动相机的行人街景 | 960×540 / 30 / 654 / 21.8秒 | [MOT17-10-FRCNN-browser-h264.mp4](datasets/street-video/mot17/MOT17-10-FRCNN-browser-h264.mp4) |

两个原始预览为MPEG-4 Part 2编码，不适合直接作为当前Chrome的回放输入。
浏览器使用显式转换的H.264/MP4副本，转换参数为
FFmpeg 7.1、libx264、CRF18、yuv420p、High profile、原PTS透传和faststart；不剪辑、不缩放、
不循环、不叠框、不调整帧率。转码改变像素，不能声称字节或图像完全相同。
离线转换工具使用 `imageio-ffmpeg==0.6.0`，与后端和推理环境依赖分开管理。
页面、启动器和算法不执行自动转码或编解码降级。

原片和副本的尺寸、帧数及FPS保持一致。官方来源URL、每个文件的字节数/SHA256和
可移植转码参数统一放在 [SOURCES.json](datasets/street-video/SOURCES.json)。
逐帧解码、时延和浏览器事件由运行工具写入本地JSON，不随输入视频维护。
验收方法见 [本机MVP说明](../LOCAL-MVP.md#验收方法与边界)。

[官方MOT17Labels.zip](https://motchallenge.net/data/MOT17Labels.zip) 需单独准备，不属于
仓库视频素材。其 `train/MOT17-10-FRCNN/gt/gt.txt` 包含17,450条标注，其中有效行人
12,839条、57个轨迹ID，帧号1–654；07的公开测试真值不在包中。
官网和 `seqinfo.ini` 的原始JPG帧集是1920×1080，而下载预览已缩至960×540。
正式评估须下载原始帧、对齐帧号/时间和坐标，使用既定评价协议；不能把当前回放结果直接
称作标准MOT得分。DPM/FRCNN/SDP是同一场景的三种公共检测输入，不作为三段独立视频。

当前两个副本用于现有 `/integration/video/` 本地入口，继续保持最长边640、JPEG80、
confidence=0.25、原生FP32及既有会话/在途预算。跟踪、运动状态和风险提醒可观察其输出
与生命周期；这些素材不能证明米制距离、速度、TTC和风险判断的准确率。后续需要深度真值时，
再按下方KITTI/Cityscapes或JRDB的相机标定与真值对应关系设计独立评估适配。

## 需要多少数据

下面的小样本数量是工程建议，不是统计上的最低保证，也不会被脚本自动抽样或划分：

| 阶段 | 建议数量 | 能回答的问题 |
| --- | --- | --- |
| 功能检查 | 5–20 张不同场景照片；随附 1 张可先验证启动 | 两个模型能否加载、框/距离/标注是否输出，异常是否明确 |
| 初步对比 | 固定 100–300 张独立场景照片 | 在同一批输入下比较输出、耗时、失败情况；有真值才可比较准确率 |
| 正式检测对比 | COCO val2017 全部 5,000 张 | 使用统一类别、官方检测标注及评价协议比较检测质量 |
| 正式室内深度对比 | NYU Depth V2 的标准 654 张测试图 | 使用统一深度真值、有效像素掩码和协议比较深度误差 |
| 手机场景补充 | 50–100 个独立场景，记录目标实测深度 | 检查公共数据集之外的手机相机、距离范围、光照和遮挡影响 |

连续视频的相邻帧或 `--repeat` 重复同一图片不等于新增独立样本；后者仅用于观察重复运行。
模型差距很小时，100 张可能不足以稳定排名，应扩大到完整测试集，并按场景给出不确定性。
功能测试不需要标签；准确率测试需要检测框/类别真值以及与 RGB 对齐的米制深度真值。

## 找到的公开数据集

| 数据集与官方入口 | 规模与下载建议 | 可以评估什么 |
| --- | --- | --- |
| [COCO](https://cocodataset.org/#download) | val2017：5,000 张，图片约 1 GB；train/val 标注约 241 MB。参考 [COCO-Stuff 官方下载表](https://github.com/nightrome/cocostuff)。 | 通用目标检测；没有真实米制深度，不能用它证明距离准确率。 |
| [NYU Depth V2](https://cs.nyu.edu/~fergus/datasets/nyu_depth_v2.html) | 1,449 对对齐 RGB/深度图，有类别及实例标注；完整 labeled MAT 约 2.8 GB。先用 labeled，无需下载约 428 GB 的 raw。 | 室内深度；原始实例标注可定义目标区域，配合类别映射进行对象距离评估。 |
| [KITTI Depth Prediction](https://www.cvlibs.net/datasets/kitti/eval_depth.php?benchmark=depth_prediction) | 官方提供约 93,000 张深度图；深度标注包约 14 GB，RGB 必须配对。不同 benchmark/Eigen 划分不能混用。 | 室外道路/车辆场景的稀疏 LiDAR 深度；只在有效真值像素上评估。 |
| [Cityscapes](https://www.cityscapes-dataset.com/dataset-overview/) | 精细标注 5,000 张，其中 val 500 张；下载需要注册。取对应 RGB、实例标注、disparity 和 camera 文件。 | 街景行人/车辆实例及深度对照；其视差是立体算法结果，不能当作高精度 LiDAR 真值。 |

NYU 最适合先做室内距离对比。其原始 MAT 中 `depths` 是补洞后的米制深度，`rawDepths`
是投影到 RGB 的未补洞米制深度；必须固定选择和有效掩码。[原始字段说明](https://cs.nyu.edu/~fergus/datasets/nyu_depth_v2.html)
Ultralytics 还提供约 1.5 GB 的 [NYU PNG 转换包](https://github.com/ultralytics/assets/releases/download/v0.0.0/nyu-depth-png.zip)：
795 张 train、654 张 val（标准 Eigen test），RGB 在 `images/val`，深度在 `depth/val`，
深度 PNG 是 uint16 毫米，除以 1,000 才是米。这个便捷格式用于 RGB/深度配对；
对象实例标注仍从原始数据获取。[转换包说明](https://docs.ultralytics.com/datasets/depth/nyu-depth-v2)

KITTI 的 [Ultralytics 格式](https://docs.ultralytics.com/datasets/depth/kitti) 当前取 652 张具有 improved GT
的 Eigen 测试图，PNG 除以 256 得到米；完整自动转换会下载约 175 GB RGB 和 14 GB 深度。
官方说明已发布权重曾使用包含其中 72 张测试帧的旧训练划分，所以这些权重的 KITTI 结果不能
直接宣称完全独立测试。记录具体权重、划分版本，并补充独立实拍场景。

Cityscapes 的实例类别、500 张 val、视差编码和相机标定见 [官方脚本说明](https://github.com/mcordts/cityscapesScripts)。
视差需要依据数据集的立体相机标定转换，原始整数不是米。该转换只用于建立测试参照；
本项目预测距离始终来自 YOLO26 Depth，没有新增框大小或焦距启发式。

## 下载后如何用当前脚本

大型数据放在 `backend/model_tests/datasets/`。除上述4个测试视频、来源清单和目录说明外，
完整数据集、标注及生成结果仍被Git忽略；不会自动下载缺少的文件。
以下命令从 `backend/model_tests` 执行，模型权重继续共用 `backend/models/`：

```powershell
# 随附示例，不需要下载数据集
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --output outputs/bus-run-3

# 自己的 RGB 图片目录：全部递归处理，不抽样
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/my-photos --output outputs/photos-run-1

# NYU PNG 包解压后的 RGB 测试目录
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/nyu-depth-png/images/val --output outputs/nyu-n-run-1

# 显式指定已准备好的另一份兼容 YOLO26 Depth 权重
..\.venv-inference\Scripts\python.exe -X utf8 .\run_depth.py --input datasets/nyu-depth-png/images/val --depth-weights ../models/yolo26s-depth.pt --output outputs/nyu-s-run-1 --label yolo26s-depth
```

`--input` 必须指向 RGB 图片或 RGB 目录；不要指向同时包含 RGB、深度、掩码的数据集根目录。
脚本会递归读取 jpg/jpeg/png/bmp/webp，不会自动识别哪些 PNG 是真值。
输出目录必须是新目录；失败时退出码为 1，已开始的运行保留 `status=failed` 和部分结果，不重试。
完整公开数据集不随仓库提供，使用前按官方入口准备对应文件。

当前检测加载器固定验证原有 YOLO26n 权重与 manifest，`--weights` 不是任意检测模型的通用接口。
兼容的 YOLO26 Depth 权重可通过 `--depth-weights` 显式选择。
后续若对比其他检测架构，需要单独的评估适配层，保留现有生产推理契约。

## 正式对比时固定哪些条件

- 固定相同测试集和划分、RGB/真值对应关系、类别映射、输入/权重 SHA256、依赖版本、GPU、精度、输入尺寸。
  当前脚本记录哈希、预处理设置和逐帧元数据，便于追溯；不会更改实验种子、划分或现有评价指标。
- 当前输入先按原有相机流程缩至最长边 640，再编码 JPEG80；`bbox` 属于该帧坐标。
  导出同时记录 `source_size` 与 `frame_size`，对接真值时必须按相同比例对齐，不能直接使用原图坐标。
- 当前的 confidence=0.25 是功能推理配置。正式 COCO AP 要接入项目既有评价流程，
  不能拿这些已阈值截断的输出直接冒充标准全量 mAP。
- 深度比较需明确有效像素、裁剪、最大深度、是否尺度对齐。建议米制距离单独报告未按真值
  调整尺度的 MAE/AbsRel/RMSE；这些是后续协议建议，当前脚本尚未计算。
  [Ultralytics NYU 说明](https://docs.ultralytics.com/datasets/depth/nyu-depth-v2) 的发布指标使用 TTA 和真值尺度对齐，
  内置 validator 也有 median alignment；这种分数不能直接证明原始输出的实际米数准确。
- 对象距离需预先定义同一真值框/实例区域，或固定检测框与真值的匹配规则，同时报告漏检和无有效距离比例。
  只比较每个模型各自检测到的目标会让漏掉困难目标的模型看起来更好。NYU/Cityscapes 类别也需映射到检测类别。
- 实拍测量要区分沿相机光轴的深度与到目标的斜向直线距离；当前输出是光轴深度。
  框中包含背景/遮挡时，中位数可能不代表目标表面，需保留这类样本观察误差。
- 当前 `predict_wall_ms` 是本地一次完整 `predict()` 的墙钟耗时，含首次 Graph/predictor 初始化，
  不含模型加载、输入预处理和文件保存。所有帧均保留，不自动丢弃首帧；不能倒数后冒充视频 FPS。
  本地 FP32 与现有生产 compiled-FP16 是不同运行条件，性能报告应分别记录。

安装和运行见 [README.md](README.md)，融合实现与 GPU 验证见 [DEPTH.md](../DEPTH.md)。后续正式评价应增加读取真值及
指标计算的专门脚本，并使用预先确定的协议和项目既有评价配置。
