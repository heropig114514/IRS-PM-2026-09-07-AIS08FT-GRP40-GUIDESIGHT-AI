# Frontend

手机摄像头回传和 Leaflet/OSM 步行路线页面，采用原生 HTML/CSS/JavaScript，无 npm 运行依赖。摄像头页只加载本地资源；独立地图页加载固定 SRI 的 Leaflet 1.9.4。Django 从文件白名单提供模块，开发地址 `http://127.0.0.1:8766/`；服务器地址 `https://47.239.50.129/prs/`。

## 使用

在手机 Safari/Chrome 等系统浏览器中打开服务器链接，先点“测试连接”，再选择前置或后置镜头并点击“开启摄像头并回传”。允许摄像头后，本地预览显示采集内容，另一屏显示真正经过服务端返回的 JPEG。停止、设备中断、页面隐藏或断线会释放摄像头与连接，重新开始须再次点击。

默认最大边 640 像素、JPEG 质量 0.8，每帧完成回传后等待 200 ms；最大速率约 5 fps，实际速率还包括网络、编码和解码耗时。这是连续图像帧回传，不是音视频录制或模型推理。选定镜头使用 exact facingMode，不存在时明确报错，请手动选择另一镜头；不会自动切换。

## 模块与边界

- `camera.js` 管理媒体、页面和串行采集；`generation` 防止停止后的异步结果恢复采集或回显。
- `echo-client.js` 管理单个在途操作。二进制长度与每个字节必须一致；pong 必须具有相同 id。超时、类型错误、内容不一致或断线均停止，不重试或降级。
- `index.html` 和 `camera.css` 提供手机单列、桌面双列布局。
- `tests/echo-client.test.mjs` 用明确的 socket mock 检查协议；不代表真实摄像头或公网已验证。

执行 `node --test tests/*.test.mjs` 检查客户端契约（Node 22+）。浏览器权限与实际手机兼容性需要真机确认。

浏览器摄像头需要 HTTPS，localhost 为本地开发例外。不要通过手机访问 HTTP IP 地址或证书警告页面来申请摄像头。页面只申请 video，不申请 microphone，不写入 IndexedDB/localStorage 或下载文件。

后端 API 前缀为 `/api/`，服务器代理后为 `/prs/api/`。诊断 WebSocket 为 `/ws/echo/`，服务器为 `/prs/ws/echo/`。当前服务匿名开放回传但校验同源，并限制 4 个连接和 1 MiB 单帧；这不是身份认证系统。部署代理清除 Cookie/Authorization，诊断入口与现有应用会话隔离。业务账号与上传功能须后续独立设计。

## 步行路线与设备定位

进入 `/prs/maps/`（开发为 `/maps/`），点击获取设备起点并允许定位，点选地图终点，再点击“规划步行路线”。也可手动填写经纬度。测试接口可点击“填入 NUS 测试示例”再规划；示例标签明确不是设备位置，初始地图只是 NUS 区域预览。浏览器可能返回粗略定位，以显示精度为准。定位与底图状态分别报告，失败不会用 IP 或示例位置替代，不自动重试。

`maps-client.js` 管理定位、坐标和同源路线请求契约；`maps-page.js` 管理 Leaflet 图层与界面。浏览器使用 `[经度,纬度]` 的路线坐标，转换为 Leaflet `[纬度,经度]` 显示。`POST ./route/` 超时 25 秒；key 只在后端，配置接口不返回凭据。获取位置只填本页；明确规划时起终点发送 PRS/ORS，不写数据库、localStorage 或文件。ORS 英文 instruction 保留，类型标签中文显示。当前没有连续 GPS 跟踪、偏航重算、语音或 Guidance Manager 风险调度。

地图页使用独立 nonce CSP、固定版本及 SRI；摄像头页策略不变。Leaflet 来源见[官方发布页](https://leafletjs.com/download.html)，ORS 契约见[官方路线文档](https://giscience.github.io/openrouteservice/api-reference/endpoints/directions/requests-and-return-types)。正常浏览器交互加载 `https://tile.openstreetmap.org/{z}/{x}/{y}.png`，保留 Referrer、缓存和可见 OpenStreetMap 署名；不预下载、不做离线或批量瓦片扫描，遵循 [OSM 瓦片政策](https://operations.osmfoundation.org/policies/tiles/)。托管瓦片与 ORS 的生产额度/许可须按实际用途评估。`tests/maps-client.test.mjs` 使用 fake 验证失败和契约，不能证明真实网络、ORS 配额或手机 GPS。服务端 key 配置见运维说明。
