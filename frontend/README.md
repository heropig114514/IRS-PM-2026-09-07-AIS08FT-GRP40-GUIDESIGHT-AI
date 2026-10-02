# Frontend

手机摄像头回传和 Leaflet/OSM 步行路线页面，采用原生 HTML/CSS/JavaScript，无 npm 运行依赖。摄像头页只加载本地资源；独立地图页加载固定 SRI 的 Leaflet 1.9.4。Django 从文件白名单提供模块，开发地址 `http://127.0.0.1:8766/`；服务器地址 `https://47.239.50.129/prs/`。

## 使用


在手机 Safari/Chrome 等系统浏览器中打开服务器链接，先点“测试连接”，再选择前置或后置镜头并点击“开启摄像头并回传”。允许摄像头后，本地预览显示采集内容，另一屏显示真正经过服务端返回的 JPEG。停止、设备中断、页面隐藏或断线会释放摄像头与连接，重新开始须再次点击。

默认最大边 640 像素、JPEG 质量 0.8，每帧完成回传后等待 200 ms；上限约 5 fps，实际速率还包括网络、编码、解码和可选模型推理耗时。选定镜头使用 exact facingMode，不存在时明确报错，请手动选择另一镜头；不会自动切换。

处理模式默认“原样回传”。选择“YOLO26 检测 · 80 类”再开启摄像头，返回画面显示框、英文类别、置信度，目标数和推理时长显示在页面。固定 COCO 类别无需额外训练，并不包含任意道路风险类别。预计目标服务器约 1 fps，真机仍须验证。“测试示例识别”只发送公开公交车图，明确标注非摄像头，结果不代表实拍或通用准确率。详细契约见 [检测说明](../backend/VISION.md)。

## 模块与边界

- `camera.js` 管理媒体、页面和串行采集；`generation` 防止停止后的异步结果恢复采集或回显。
- `echo-client.js` 管理单个在途操作。二进制长度与每个字节必须一致；pong 必须具有相同 id。超时、类型错误、内容不一致或断线均停止，不重试或降级。
- `detection-client.js` 管理同源 HTTP 配置/标注请求，CPU 超时 10 秒、ZeroGPU 超时 60 秒，并校验 JPEG、模型、目标数和时长；停止丢弃迟到结果，不退回原样模式。`tests/detection-client.test.mjs` 使用明确的 fetch 替身，不能代替真实模型验证。
- `index.html` 和 `camera.css` 提供手机单列、桌面双列布局。
- `tests/echo-client.test.mjs` 用明确的 socket mock 检查协议；不代表真实摄像头或公网已验证。

执行 `node --test tests/*.test.mjs` 检查客户端契约（Node 22+）。浏览器权限与实际手机兼容性需要真机确认。

浏览器摄像头需要 HTTPS，localhost 为本地开发例外。不要通过手机访问 HTTP IP 地址或证书警告页面来申请摄像头。页面只申请 video，不申请 microphone，不写入 IndexedDB/localStorage 或下载文件。

后端 API 前缀为 `/api/`，服务器代理后为 `/prs/api/`。诊断 WebSocket 为 `/ws/echo/`，服务器为 `/prs/ws/echo/`。当前服务匿名开放回传但校验同源，并限制 4 个连接和 1 MiB 单帧；这不是身份认证系统。部署代理清除 Cookie/Authorization，诊断入口与现有应用会话隔离。业务账号与上传功能须后续独立设计。

## 步行路线与设备定位

进入 `/prs/maps/`（开发为 `/maps/`），点击获取设备起点并允许定位，点选地图终点，再点击“规划步行路线”。也可手动填写经纬度。测试接口可点击“填入 NUS 测试示例”再规划；示例标签明确不是设备位置，初始地图只是 NUS 区域预览。浏览器可能返回粗略定位，以显示精度为准。定位与底图状态分别报告，失败不会用 IP 或示例位置替代，不自动重试。

`maps-client.js` 管理定位、坐标和同源路线请求契约；`maps-page.js` 管理 Leaflet 图层与界面。浏览器使用 `[经度,纬度]` 的路线坐标，转换为 Leaflet `[纬度,经度]` 显示。`POST ./route/` 超时 25 秒；key 只在后端，配置接口不返回凭据。获取位置只填本页；明确规划时起终点发送 PRS/ORS，不写数据库、localStorage 或文件。ORS 英文 instruction 保留，类型标签中文显示。当前没有连续 GPS 跟踪、偏航重算、语音或 Guidance Manager 风险调度。

地图页使用独立 nonce CSP、固定版本及 SRI；摄像头页策略不变。Leaflet 来源见[官方发布页](https://leafletjs.com/download.html)，ORS 契约见[官方路线文档](https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/requests-and-return-types)。正常浏览器交互加载 `https://tile.openstreetmap.org/{z}/{x}/{y}.png`，保留 Referrer、缓存和可见 OpenStreetMap 署名；不预下载、不做离线或批量瓦片扫描，遵循 [OSM 瓦片政策](https://operations.osmfoundation.org/policies/tiles/)。托管瓦片与 ORS 的生产额度/许可须按实际用途评估。`tests/maps-client.test.mjs` 使用 fake 验证失败和契约，不能证明真实网络、ORS 配额或手机 GPS。服务端 key 配置见运维说明。

## ZeroGPU 模式

在处理模式明确选择 Hugging Face ZeroGPU，先点击“测试示例识别”。手机经 PRS 云服务器把帧交给 Hugging Face，标注 JPEG 经 PRS 返回；该模式单请求最多60秒，排队、配额或异常都会停止。CPU 仍10秒；原样回传仍默认。无需本地 GPU 配置，免费资源适合低频演示；连续摄像头会持续消耗配额。停止会取消浏览器请求，但服务端已提交的 GPU 任务可能继续完成。
