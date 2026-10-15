# 摄像头前端

本地MVP由 `integration.asgi` 启动，默认入口 http://127.0.0.1:8766/live/ ，启用独立的
**本地 CUDA** 模式与浏览器Guidance。原 `config.asgi` 页面保留Echo/HF，本地选项禁用。
准备、两个环境、启动器、验证目标与设备边界见 [本地MVP](../backend/LOCAL-MVP.md)。
Guidance默认关闭，消费服务端风险字段；用户启用后复用现有语音/振动控制器，
手动语音与振动测试仍可独立使用。导航示例用于仲裁联调，不代表GPS或路线导航验证。

连续视频入口为 http://127.0.0.1:8766/integration/video/ 。独立页面只加载
`integration/video-mvp.js`、`video-replay.js`和现有检测传输、流水线、语音/震动与Guidance模块，
不加载摄像头控制器、场景、地图或AI控制。用户明确选择本机视频后点击“开始本地检测”；
原文件以Blob保留在浏览器，浏览器只发送按原640/JPEG0.8/1MiB契约编码的采样帧。
不请求摄像头/麦克风权限，不上传原视频，不隐式下载、转码或替换不支持的编解码器。
成功读取元数据后清空文件选择控件值，同时保留controller中的File及状态所示文件名，
这样用户可重新选择同一个已播完的文件；取消文件选择保留当前来源。读取期间可显式停止，
取消会结算等待、移除监听并释放所属Blob，旧加载回调不能改变新选择。

视频固定1倍速、无循环/跳转控件，采样由真实`requestVideoFrameCallback`的新呈现帧驱动。
先取得采集额度再绘制，输入上限30FPS；编码、传输及等待顺序显示的总窗口为12，
首帧结果独占期间视频仍向前播放，不重复静止图像。浏览器时钟的`capturedAt`在实际
`drawImage`前读取并沿PRF2送入算法；`sourceMediaTime`仅观测原视频呈现时间，
不会代替算法的motion/TTC时间戳。速度、循环、跳转或重复/倒退呈现时间明确停止，不修正或重试。
不同的源视频时间跨度、浏览器采集跨度及回传延迟分别记录，不能把文件回放视为物理摄像头验证。

自然EOF或显式调用`videoReplay.finish(reason)`会停止新采集、等待已获额度的编码及全部已发送
结果排空后停止client和Guidance；用户“停止”、页面隐藏、离页或错误立即取消自身任务，
晚到编码/解码不重新发送或更新结果。原55秒服务端上限保持，provider正常结束不自动重连；
未发送编码及未交付帧诚实计数。需要重新开始时由用户手动操作，不自动倒带或循环。
视频页面的本地引导仍默认关闭；风险直接消费服务端结果，另须明确开启原语音/震动通道，
声音须匹配语言且`local_service=true`。demo导航、TTL、优先级、冷却及请求所有权沿用下述Guidance契约。

`videoReplay.subscribe(listener)`提供`session/frame/submitted/stop/ended/error/loaded`事件；
`frame`携带原始`capturedAt`、`sourceMediaTime`、`presentedFrames`、metadata及完整计时。
`stats`区分`sourceCallbacks/observedPresentedFrames/callbackGaps`、未采集的
`skippedCadence/skippedCredit/skippedFirstFrame`、`admitted/encoded/sent/delivered`、
`cancelledBeforeSend/maxInFlight/currentInFlight`和源PTS范围；原生callback计数跨越的呈现帧
计入gap，采样前跳过的源帧不会先采集再丢弃。入口导出`videoReplay/guidanceManager/videoMvpReady`，
便于验证工具订阅真实事件。Node的媒体/编码/传输替身不证明真实MP4解码、GPU或实际声响/马达。

运行连续视频真实Chrome/GPU验证工具时，需先启动本地双服务并设置`PRS_PLAYWRIGHT_PATH`；
从`frontend`执行以下命令（报告路径须不存在，视频须为明确选定的本地文件；转码副本须保留来源及处理记录）：

```powershell
node tools/check_local_video_browser.mjs http://127.0.0.1:8766/integration/video/ NEW_REPORT.json LOCAL_VIDEO.mp4
```

工具检查原生File/Blob解码、多帧严格递增源PTS/采集关联、一次用户停止与一次自然EOF，
保留每帧与所有失败；明确选择匹配本地声音，缺少声音则记为不可用，不伪造风险或引擎start。
视频回放素材包括MOT17-07白天和MOT17-10夜间移动相机序列，各自提供原始预览与H.264
浏览器副本。用户选择 `*-browser-h264.mp4`；页面不执行自动转码。4个视频及来源清单属于
版本管理范围，完整标注及逐帧运行报告单独管理。素材属性见 [视频数据说明](../backend/model_tests/DATASETS.md#连续街景视频用于本地联调)，
验收方法见 [本地MVP说明](../backend/LOCAL-MVP.md#验收方法与边界)。

本地操作顺序：先读取摄像头列表；若首次权限导致deviceId为空，点击“授权并读取摄像头列表”，
该显式操作仅申请video权限并立即停止临时tracks，不启动检测、不选择默认设备。
授权后仍须明确选择摄像头，保持“本地CUDA”模式再点击原摄像头开始按钮；
要播报时，另外勾选原“启用语音输出”，在本地Guidance区选择中/英文及匹配的本地声音，
再勾选“启用提醒”。可按需勾选原振动开关。Guidance不会自动开启设备输出或选择默认/远程声音；
设备或声音消失时不替换，通道错误保持暂停，修正配置后由用户点击对应恢复按钮。

`integration/guidance.js` 只保存最新感知快照，按原采集时间判断1秒新鲜度；
旧结果仍显示并计入性能统计，但不新建提醒。只在服务端 `alert_required=true` 的对象中，
按优先级降序、当前距离升序、track ID升序选一个；不补告警、不改变风险阈值。
同会话/tracking epoch/track同级提醒从真实语音引擎start或振动API接受开始冷却3秒，升级可立即提醒，
降级不产生新的重复提醒。风险可打断Guidance拥有的低优先级/导航输出，不取消手动输出；
手动振动请求未明确取消前，Guidance不提交新的风险振动。
关闭Guidance、停止视频、会话结束或隐藏页面会取消自身请求并清除状态，不重试或自动恢复。

导航示例按钮生成明确标记为demo的指令，只保留一条待播报指令，原始有效期10秒；
风险中断不延长有效期，过期丢弃，无FIFO积压、GPS、定位或路线请求。
语音提交和引擎start、振动API接受及节奏计时均是软件事件，不证明实际声音/马达已验证。
批准目标为≥10FPS且采集→告警<500ms；采集→解码、采集→真实引擎start及实际声音/马达
分别记录，不用解码或替身事件替代告警实测。请求保存原始源帧上下文，后续新帧不刷新其时钟。

页面提供独立“语音输出测试”：默认关闭，可输入文本、填入中英文示例、选择设备声音、
语速0.5至2及音量0至1，再朗读/停止。只调用浏览器SpeechSynthesis，不申请录音或摄像头，
不生成音频文件、不自动启用检测提醒或替换失效声音。语音服务是否联网以设备声音的
`localService`属性为准，UI以`[local]/[remote]`标明原生身份。
`speech-output.js`的共享`SpeechOutput`接口负责`describe/voiceList/speak/stop`，
MCP提供`speech_status/speech_voices/speech_speak/speech_stop`，须明确指定tab。
用户开关、前台和真实激活要求保持，MCP不自动启用。文本最多1000个Unicode码点，
状态区分提交、引擎开始、结束、取消和错误；实际扬声器声音始终待设备确认。
取消或旧对象的迟到回调不能更新新请求；取消异常保留未确认状态并明确提示，不重试。
正文只用于当前utterance，不保存或记录进应用日志；英文UI不翻译用户输入/原生声音名称。
接口参数、机器可读错误和调用示例见[语音MCP契约](../backend/MCP.md)。

真实Chrome/UI/MCP回归从frontend执行（需先在8768启动单worker ASGI/lifespan）：
`node tools/check_speech_browser.mjs http://127.0.0.1:8768/live/ NEW_SPEECH_REPORT.json`。
设置`PRS_PLAYWRIGHT_PATH`及后端解释器`PYTHON`。测试使用明确原生引擎替身验证UI/参数/事件/错误，
保留真实用户激活及HTTP/WS链路，检查中英切换和320/390/1280px布局；没有数据库写入或GPU调用。
另以真实本地英文语音提交一次短样例，独立记录引擎结果；无此声音/超时会明确报告。
可在同一命令末尾显式加`--native-chinese-only`并使用另一个输出路径，独立验证本地中文，
保留原英文结果。各测试按明确语言选择本地声音，不互相替代，
不选择默认或远程声音来制造通过。所有结果均不证明物理声音已被听到。

摄像头和地图均加载 `ai-control.js`，通过 `/ws/control/` 向 MCP 注册当前 tab。
AI 可读取控件和状态、点击/填表/选择/提交、地图选点及跨页面跳转，既有控制器处理实际业务。
断线后手动刷新，浏览器权限要求保持；操作与验证方法见 [MCP 说明](../backend/MCP.md)。

摄像头页新增独立“手机震动测试”：默认关闭，勾选启用后可手动测试短震200ms、长震800ms、
双震150ms/暂停100ms/震动150ms；“停止震动”在接口可用时始终可操作。
`vibration.js` 不开启摄像头、不联网、不自动启用检测提醒，也不保存开关偏好。
关闭测试、页面隐藏或离开时，对曾被接受且尚未取消的请求发送 `navigator.vibrate(0)`。
接口缺失、拒绝和异常分别显示诊断并记录日志，不自动重试或替代为其他提醒。
“请求已接受”只说明浏览器返回true，不能证明马达实际震动；安卓Chrome等可测试，
iPhone Safari不支持。请在真实手机前台页面检查短震、长震、双震、停止及系统震动设置。
Node与桌面Chrome替身测试只验证调用和UI状态，不代表真机硬件、锁屏或后台提醒能力。
MCP提供 `vibration_status`、`vibration_test`、`vibration_stop`，通过当前tab的控制连接
复用同一个 `VibrationTester`。调用必须明确指定session，测试保留用户开关、前台和真实交互要求，
不自动启用或重试；结果使用稳定的 `accepted/reason` 字段，详情与例子见[震动MCP契约](../backend/MCP.md)。
真实HTTP/WS/Chrome联调从frontend执行
`node tools/check_vibration_mcp_browser.mjs http://127.0.0.1:8768/live/ NEW_VIBRATION_MCP_REPORT.json`，
需在8768启动现有单worker ASGI，并设置 `PRS_PLAYWRIGHT_PATH` 与后端解释器 `PYTHON`。
验证默认关闭、合成事件不能授予激活、真实点击后的三个节奏及停止、原生拒绝/异常和接口缺失。
原生震动/媒体API是显式替身；用户激活与MCP HTTP/WS为真实路径，不写数据库、不调用GPU。
设好 `PRS_PLAYWRIGHT_PATH` 后，从frontend执行
`node tools/check_vibration_browser.mjs http://127.0.0.1:8766/live/ NEW_VIBRATION_REPORT.json`，
在真实Chrome验证支持/缺失/拒绝/异常、默认关闭及停止、零媒体授权调用、中英切换和320/390/1280px布局。
输出JSON和手机宽度区域PNG；原生震动和媒体API在测试中明确替换，不能据此声称真机马达已验证。

本地集成入口默认 http://127.0.0.1:8766/live/ ，公网路径示例 https://47.239.50.129/prs/live/ 。
原 `/prs/` 转到同页。Processing mode提供：

- **Echo／原样回传**：`/ws/echo/`，用于检查摄像头与网络，返回输入原始字节，不进行检测。
- **本地 CUDA**：仅独立integration入口启用，`/ws/detect/local/` 转到独立推理进程；本机双模型、BoT-SORT、TTC/规则风险，返回标注JPEG与对象元数据。
- **Hugging Face ZeroGPU**：`/ws/detect/hf/`，最新视频检测，返回 YOLO26n 标注 JPEG。

`camera.js` 管理媒体、停止状态和计时；`echo-client.js` 校验原样回传；
`live-detection-client.js` 校验二进制序号、模型/80类/原权重 SHA、框和 JPEG SHA，
并要求每帧计算精度、后端与所选模式的会话声明一致；FP32输出与计算精度分别标记。
本地声明为原生FP32 CUDA，不伪装HF编译模式；HF服务端读取显式方案文件，至少三个独立完整5000图 COCO验证的 mAP下降均不得超过0.5个百分点；
客户端验证其公开证据字段，不能据此声称自己重新测量了mAP。
`frame-pipeline.js` 管理采集及顺序解码显示。摄像头最大边640、JPEG质量0.8。
公开页面在首帧独立完成后使用30FPS输入上限，最多12个采集/传输/等待显示作业，
先取得空位再采集。PRS/HF首帧返回前执行一次512KiB双向传输预热，耗时计入首帧，
共1MiB额外流量。客户端、PRS relay和HF parent严格要求同一12帧协议，须协调发布；
窗口包含网络在途帧，并不要求预先积压12张待推理图。
调度按预定时间线推进，普通定时器晚醒不逐帧累积；等待信用或错过完整采样间隔后重新计时，
不重放错过的采样槽。每5秒及停止时汇总定时器晚醒和信用等待，区分采样与网络窗口限制。
没有额外200ms等待、旧帧积压、自动重连或自动更换模式。

一次 ZeroGPU 视频最长55秒，结束或出错后手动重新开始。免费额度不足时拒绝申请；
模型参数和评估目标不变。画布尺寸不变时复用；exact facingMode 失败明确提示，不自动切镜头。
页面 API 挂载点只允许同源相对路径，HF token 留在服务端。

`node --test tests/*.test.mjs` 执行客户端协议、状态、调度、语言和地图检查。
`tools/benchmark_phone_browser.mjs URL FRAMES OUTPUT` 使用实际 Chrome、CameraApp、JPEG编码、
WS与解码，以开发目录 `tools/fixtures/detection-sample.jpg` 替代物理传感器。
图片不部署为公开资源，原字节保留；测试不代表真实手机传感器、无线网络或最终屏幕绘制。
诊断可显式加`--pipeline-file PATH`对照本地调度源码，报告其SHA；默认使用公开部署源码。
`tools/profile_frame_pipeline.mjs URL OUTPUT PIPELINE_FILE [--cpu-profile]`执行真实浏览器
12/24FPS、0/250ms合成Echo控制，不调用HF/GPU；合成传输不能代替公网推理结论。
CPU采样默认关闭，因为profiling本身可能影响定时器精度；开启时保存原始CPU profile。
`tools/benchmark_phone_windows.mjs URL OUTPUT [--echo-delay MS] [--30fps]`默认保留12/24FPS和5/8帧对照。
默认以一个真实HF连接/一次GPU分配运行四个明确分段并保留每一帧；各段首帧单列并完整排空。
`--echo-delay 300`为本地源码、实际Chrome编码/解码/HUD和合成传输的零GPU控制，不作推理结论。
`--30fps`显式选择24/8、30/8、30/12、30/12、30/8、24/8六段平衡顺序，共606帧；
对应61/91/151/151/91/61帧，30/12两个约五秒测试段保留，缩短对照段减少HF调用时长。
结合`--echo-delay`可做本地合成控制，真实模式使用已协调发布的12帧公开协议。
测量分别报告全程FPS、相同非首帧集合的完成间隔FPS、实际采集FPS和信用等待总时长；
完成间隔可能受端点延迟变化影响，不能替代原全程延迟验收。
入口在任何媒体或provider启动前校验所加载客户端的窗口，八帧部署运行十二帧测试会明确
拒绝，不会部分运行后才失败或自动改成较小窗口；源码指纹和实际上限都进入报告。
该工具不自动重试，真实HF模式需先检查90秒预留额度。
本地原生Graph基准由后端 `tools/benchmark_phone_native.py` 启动，需显式传入完整验证的
eager Graph方案 `--plan`；该native控制不会加载AOT包。公网证据见
[PHONE-LIVE.md](../backend/PHONE-LIVE.md)。已有地图页面和模块保持原契约。

实时结果支持 BoT-SORT 的 `track_id/motion_state/relative_speed_mps/smoothed_distance_m`；
客户端校验并保留新增字段，展示服务端包含 ID、距离和运动状态的标注 JPEG，继续接受旧字段。
已确认运动的标签增加带符号 m/s；短暂缺距离允许保留服务端未过期的历史运动估计。
JSON 上限256KiB、JPEG1MiB，服务端包上限1310728字节，须与 HF/PRS 协调发布。
跟踪生命周期、手机运动补偿、运行配置及验证局限见 [TRACKING.md](../backend/TRACKING.md)。
结果还支持方向、TTC秒数、risk_score/risk_level/alert_priority/alert_required/risk_reasons。
客户端校验和保留字段，high/critical服务端标注追加简洁TTC和等级。
本地Guidance在用户启用后消费告警字段，仲裁语音/振动与导航示例，不在浏览器重复评分。
缺当前深度时风险unknown、TTC为空，即使运动历史仍有效。规则配置和默认值见
[RISK.md](../backend/RISK.md)。旧包仍兼容，元数据上限保持256KiB。

摄像头与 `/prs/maps/` 地图页共用“跟随系统 / English / 中文”选择器，语言偏好同源保存。
地图的按钮、定位/底图状态、来源/精度、路线汇总、转向类型、标记提示、错误及无障碍标签
随语言即时切换。已返回的 ORS 英文原始指令与地名保持原样；切换语言不重新定位或请求路线。
发布工具同时上传地图页面/模块和共享语言资源，避免地图页遗漏语言支持更新。

真实 Chrome 回归工具使用开发运行时的 Playwright，保留 TLS/SRI/CSP 校验。
从 `frontend` 执行；`PRS_PLAYWRIGHT_PATH` 需指向已安装的 Playwright 包绝对路径：

```powershell
node tools/check_maps_browser.mjs http://127.0.0.1:8766/maps/ NEW_MAPS_REPORT.json
node tools/check_maps_browser.mjs https://47.239.50.129/prs/maps/ NEW_PUBLIC_MAPS_REPORT.json --live-route
```

默认模式以合成 API 响应和合成定位验证真实页面的语言切换、错误、跨页面偏好及390px布局，
输出 JSON 和英文页面 PNG；不代表真实 GPS、浏览器权限弹窗或 ORS 上游验证。
`--live-route` 另执行一次明确标记的 NUS 示例真实 ORS 请求，并检查既有路线切换语言时不重复请求。
失败保存诊断并退出，不自动重试或回滚。

## 场景描述（Qwen2.5-VL，feat/vlm-scene）

摄像头页的“场景描述”区块（`scene-client.js`）：开启摄像头后点击“描述场景”（可选填最多200字的提问），
浏览器向同源 `POST scene/describe/`（沿用 `data-api-base`，`/live/` 页即 `/scene/describe/`）发送
`{image_jpeg_base64, language, question?, objects?}`，显示返回的 `text`；接口契约见后端 `core/scene.py` / `backend/VLM.md`。
- 取帧：`CameraApp.latestJpeg()` 复用推流中最近一帧已编码 JPEG（640 最大边/质量0.8，≤1秒）；过期或未推流但摄像头已开启时按同一参数重新抓取一帧；摄像头未开启则提示，不申请权限。
- objects：`CameraApp.latestBoxes()` 取最近（≤1秒）一次本地/HF检测结果的 boxes（Echo模式无检测则不带objects），
  映射 `score→confidence`，携带 `distance_m/direction/risk_level/motion_state/track_id`（缺失即省略，不编造），
  按 alert_priority 降序、距离升序、置信度降序排序后截断到50。
- 语音：仅当页面“启用语音输出”已勾选时调用共享 `SpeechOutput.speak`（语言随页面语言）；否则只显示文字。
- 单次在途（按钮禁用）、30秒浏览器超时、不自动重试；错误按后端 `code` 显示中英文提示并附 `request_id`，不显示 `detail`。
- 图像仅在内存中经 HTTPS 发给服务端再转发给视觉模型，不保存。结果是 AI 生成，可能有误，不能替代白手杖等。
- 新鲜度限制：阈值为 `camera.js` 导出的 `SCENE_MAX_AGE_MS`（1000毫秒）。复用的帧与检测框可能各自最多1秒旧，且二者不一定来自同一时刻，检测框可能与图像略有错位。
- 未预期的异常只显示通用失败文案（控制台仅记录异常类型），不展示原始异常文本。
- 后端静态白名单 `core/camera.py::ASSETS` 必须包含 `scene-client.js`。
`tests/scene-client.test.mjs` 用 fetch/语音/取帧替身覆盖映射、请求体、错误与语音；真实浏览器与手机未测。
