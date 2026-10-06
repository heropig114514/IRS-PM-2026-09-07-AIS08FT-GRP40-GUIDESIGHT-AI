# PRS 摄像头部署

当前入口 https://47.239.50.129/prs/live/ 。`/prs/` 转到同一页面，仅 Echo 和最新 HF 视频路径。
部署脚本 `tools/deploy_phone_live.py` 验证固定 SSH 主机指纹，从当前 release 创建新 release，
删除明确列出的旧模式源码，覆盖本项目拥有的路由/页面/流式模块及地图前端，不改地图 API 及其他应用。
`maps.html`、`maps-page.js`、`maps-client.js` 与共享 `i18n.js`、`translations.js` 统一发布，
避免摄像头页面升级语言支持后地图页面仍停留旧版。地图语言沿用同源保存的偏好，
切换语言只更新显示，不重新定位或请求 ORS，ORS 返回的英文原指令保持原样。
先验证实际COCO合格的 `models/inference-plan.json`，再校验上传SHA、Django配置和迁移状态，
最后切换current、重启单独prs-camera服务和reload nginx。PRS与HF必须发布同一个计划，
客户端会逐帧检查计算精度/后端与会话声明一致。
不会自动回滚、重试或调用 GPU。旧 release、配置备份和已保存版本用于人工恢复。

```powershell
.\.venv\Scripts\python.exe -m tools.deploy_phone_live --stage --manifest benchmarks/2026-10-05/maps-i18n-deployment.json
.\.venv\Scripts\python.exe -m tools.deploy_phone_live --activate --manifest benchmarks/2026-10-05/maps-i18n-deployment.json
```

发布前在前端运行 `node --test tests/*.test.mjs`；浏览器回归命令和模拟边界见
[前端说明](../../frontend/README.md)。发布后检查公网地图源码哈希，并用该工具的
`--live-route` 模式执行一次明确标记的 NUS 示例真实路线请求。任何失败保留诊断，
不自动重试、回滚或切换服务。

本地 `server_info.txt` 与 `.env.hf` 是被 Git 排除的私有凭据。服务器 token 只写入 root0600
`/etc/prs-camera/app.env`。systemd 使用独立 runtime-venv、单 worker、MemoryMax256MiB、CPUQuota20%、
TasksMax32；WebSocket 队列5、帧1MiB、无压缩。Nginx 仅当前视频 WS 读超时65秒，其他 PRS 路径30秒，
关闭上游重试及缓冲，清除 Cookie/Authorization/Forwarded；公网 admin 返回404。

HF 源码发布和旧接口删除见 [ZEROGPU.md](../ZEROGPU.md)，当前测试及历史长尾见
[PHONE-LIVE.md](../PHONE-LIVE.md)。
