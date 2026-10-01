# Frontend

手机摄像头回传和 Google Maps 定位页面，采用原生 HTML/CSS/JavaScript，无 npm 运行依赖。摄像头页只加载本地资源，独立地图页按配置加载 Google SDK。Django 从文件白名单提供模块，开发地址 `http://127.0.0.1:8766/`；服务器地址 `https://47.239.50.129/prs/`。

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

## Google Maps 定位

进入 `/prs/maps/`（开发为 `/maps/`），配置有效后点击“显示我的位置”，允许设备定位。页面分别报告 SDK、定位和瓦片状态；瓦片加载后才显示“地图已显示”。定位错误、Google 认证、网络和超时均明确失败，不自动重试、不以服务器 IP 或示例坐标代替设备位置。需要在设备/浏览器开启定位服务；浏览器可能返回粗略位置，以页面显示的精度为准。

`maps-client.js` 管理 SDK/定位/瓦片契约，`maps-page.js` 管理界面。配置接口只公开网站限制型浏览器 key，不公开服务器凭据；坐标仅用于本页与 Google 地图，不发给 PRS 后端、不保存。地图页有独立 nonce CSP；摄像头页的策略不变。`tests/maps-client.test.mjs` 使用 fake 测试失败边界，不证明真实 Google 认证或设备 GPS。

Google [Maps JavaScript API key 文档](https://developers.google.com/maps/documentation/javascript/get-api-key)要求配置 key。原型也可申请[官方 Demo Key](https://developers.google.com/maps/documentation/javascript/demo-key)，无需绑定账单，但有额度和功能限制。标准 key 应启用 Maps JavaScript API，并限制网站 `https://47.239.50.129/*` 和对应 API；本地测试另加 localhost 来源。key 不能提交 Git。服务器配置见运维说明。
