# 摄像头前端

页面新增独立“语音输出测试”：默认关闭，可输入文本、填入中英文示例、选择设备声音、
语速0.5至2及音量0至1，再朗读/停止。只调用浏览器SpeechSynthesis，不申请录音或摄像头，
不生成音频文件、不自动绑定检测结果或替换失效声音。语音服务是否联网以设备声音的
`localService`属性为准，UI以`[local]/[remote]`标明原生身份。
`speech-output.js`的共享`SpeechOutput`接口负责`describe/voiceList/speak/stop`，
MCP提供`speech_status/speech_voices/speech_speak/speech_stop`，须明确指定tab。
用户开关、前台和真实激活要求保持，MCP不自动启用。文本最多1000个Unicode码点，
状态区分提交、引擎开始、结束、取消和错误；实际扬声器声音始终待设备确认。
取消或旧对象的迟到回调不能更新新请求；取消异常保留未确认状态并明确提示，不重试。
正文只用于当前utterance，不保存或记录进应用日志；英文UI不翻译用户输入/原生声音名称。
接口参数、机器可读错误和调用示例见[语音MCP契约](../backend/MCP.md)。

真实Chrome/UI/MCP回归从frontend执行（需先在8768启动单worker ASGI/lifespan）：
`node tools/check_speech_browser.mjs http://127.0.0.1:8768/live/ ../backend/benchmarks/2026-10-06/speech/browser-local.json`。
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
`vibration.js` 不开启摄像头、不联网、不自动绑定检测结果，也不保存开关偏好。
关闭测试、页面隐藏或离开时，对曾被接受且尚未取消的请求发送 `navigator.vibrate(0)`。
接口缺失、拒绝和异常分别显示诊断并记录日志，不自动重试或替代为其他提醒。
“请求已接受”只说明浏览器返回true，不能证明马达实际震动；安卓Chrome等可测试，
iPhone Safari不支持。请在真实手机前台页面检查短震、长震、双震、停止及系统震动设置。
Node与桌面Chrome替身测试只验证调用和UI状态，不代表真机硬件、锁屏或后台提醒能力。
MCP提供 `vibration_status`、`vibration_test`、`vibration_stop`，通过当前tab的控制连接
复用同一个 `VibrationTester`。调用必须明确指定session，测试保留用户开关、前台和真实交互要求，
不自动启用或重试；结果使用稳定的 `accepted/reason` 字段，详情与例子见[震动MCP契约](../backend/MCP.md)。
真实HTTP/WS/Chrome联调从frontend执行
`node tools/check_vibration_mcp_browser.mjs http://127.0.0.1:8768/live/ ../backend/benchmarks/2026-10-06/vibration/mcp-browser-local.json`，
需在8768启动现有单worker ASGI，并设置 `PRS_PLAYWRIGHT_PATH` 与后端解释器 `PYTHON`。
验证默认关闭、合成事件不能授予激活、真实点击后的三个节奏及停止、原生拒绝/异常和接口缺失。
原生震动/媒体API是显式替身；用户激活与MCP HTTP/WS为真实路径，不写数据库、不调用GPU。
设好 `PRS_PLAYWRIGHT_PATH` 后，从frontend执行
`node tools/check_vibration_browser.mjs http://127.0.0.1:8766/live/ ../backend/benchmarks/2026-10-06/vibration/browser-local.json`，
在真实Chrome验证支持/缺失/拒绝/异常、默认关闭及停止、零媒体授权调用、中英切换和320/390/1280px布局。
输出JSON和手机宽度区域PNG；原生震动和媒体API在测试中明确替换，不能据此声称真机马达已验证。

入口 https://47.239.50.129/prs/live/ ，原 `/prs/` 转到同页。Processing mode 只保留：

- **Echo／原样回传**：`/ws/echo/`，用于检查摄像头与网络，返回输入原始字节，不进行检测。
- **Hugging Face ZeroGPU**：`/ws/detect/hf/`，最新视频检测，返回 YOLO26n 标注 JPEG。

`camera.js` 管理媒体、停止状态和计时；`echo-client.js` 校验原样回传；
`live-detection-client.js` 校验二进制序号、模型/80类/原权重 SHA、框和 JPEG SHA，
并要求每帧计算精度、后端与服务端公布的 COCO 合格方案一致；FP32 输出与计算精度分别标记。
服务端读取显式方案文件，至少三个独立完整5000图 COCO验证的 mAP下降均不得超过0.5个百分点；
客户端验证其公开证据字段，不能据此声称自己重新测量了mAP。
`frame-pipeline.js` 管理采集及顺序解码显示。摄像头最大边640、JPEG质量0.8。
本地30FPS候选源码在首帧独立完成后使用30FPS输入上限，最多12个采集/传输/等待显示作业，
先取得空位再采集。公开生产页面仍为已验证的24FPS/8帧；30/12尚未部署或通过真实HF验证。
新客户端、PRS relay和HF parent严格要求同一12帧协议，须协调发布，不能只替换前端。
原24/8配置由真实HF同分配的12/24FPS、5/8帧窗口对照支持；旧报告和变更前源码快照保留，
不将不同条件包装为模型加速比。窗口包含网络在途帧，并不要求预先积压12张待推理图。
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
结合`--echo-delay`可验证当前本地候选，真实模式使用公开部署，须先协调升级到12帧协议。
原短测FPS口径保留，另报相同非首帧集合的完成间隔FPS、实际采集FPS和信用等待总时长；
完成间隔可能受端点延迟变化影响，不能替代原全程延迟验收。
入口在任何媒体或provider启动前校验所加载客户端的窗口，八帧部署运行十二帧测试会明确
拒绝，不会部分运行后才失败或自动改成较小窗口；源码指纹和实际上限都进入报告。
该工具不自动重试，真实HF模式需先检查90秒预留额度。旧五帧/12FPS报告作为原条件对照保留。
本地 RTX4060 联调由后端 `tools/benchmark_phone_native.py` 启动，需显式传入完整验证的
eager Graph方案 `--plan`；该native控制不会加载AOT包。公网证据见
[PHONE-LIVE.md](../backend/PHONE-LIVE.md)。已有地图页面和模块保持原契约。

摄像头与 `/prs/maps/` 地图页共用“跟随系统 / English / 中文”选择器，语言偏好同源保存。
地图的按钮、定位/底图状态、来源/精度、路线汇总、转向类型、标记提示、错误及无障碍标签
随语言即时切换。已返回的 ORS 英文原始指令与地名保持原样；切换语言不重新定位或请求路线。
发布工具同时上传地图页面/模块和共享语言资源，避免地图页遗漏语言支持更新。

真实 Chrome 回归工具使用开发运行时的 Playwright，保留 TLS/SRI/CSP 校验。
从 `frontend` 执行；`PRS_PLAYWRIGHT_PATH` 需指向已安装的 Playwright 包绝对路径：

```powershell
node tools/check_maps_browser.mjs http://127.0.0.1:8766/maps/ ../backend/benchmarks/2026-10-05/maps-i18n-local.json
node tools/check_maps_browser.mjs https://47.239.50.129/prs/maps/ ../backend/benchmarks/2026-10-05/maps-i18n-public.json --live-route
```

默认模式以合成 API 响应和合成定位验证真实页面的语言切换、错误、跨页面偏好及390px布局，
输出 JSON 和英文页面 PNG；不代表真实 GPS、浏览器权限弹窗或 ORS 上游验证。
`--live-route` 另执行一次明确标记的 NUS 示例真实 ORS 请求，并检查既有路线切换语言时不重复请求。
失败保存诊断并退出，不自动重试或回滚。
