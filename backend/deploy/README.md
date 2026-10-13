# PRS 摄像头部署

系统新增 [MCP AI 控制接口](../MCP.md)，生产模板地址 `/prs/mcp`。
发布前明确安装更新后的 `requirements.txt` 依赖；staging 会校验依赖与 ASGI 导入，
不会自动安装。服务模板必须使用 `--lifespan on` 和单 worker。
新 AI 后端、资源和依赖文件进入同一发布 allowlist；实际激活状态以发布清单为准。
设备输出发布显式使用 `--profile outputs --source-root <已审查快照> --runtime <独立解释器目录>`，
配合 `--git-commit <SHA>` 记录来源。该配置覆盖震动/语音/导航 UI、MCP/控制连接及其依赖，
保留服务器现有摄像头/推理流水线源码、模型清单和方案；两份模型元数据须与当前原字节一致。
解释器位于 `/opt/prs-practice/runtimes/`，从旧环境复制后只在新环境安装精确的直接依赖，
记录实际解析版本和 pip check；原 `/opt/prs-practice/runtime-venv` 保留不变。
服务模板的解释器路径由暂存工具显式渲染，清单同时记录 Git 模板 SHA 和最终文件 SHA。
激活前校验 current 和全部文件哈希，备份专用 service/nginx/env 后设置
`PRS_MCP_PROFILE=outputs`，其余环境字段和凭据保持；只重启 prs-camera 并检查健康、reload nginx。
当前源码的 `outputs` 目录暴露会话发现、七项输出工具及三项导航工具（共11项）；
导航工具返回设备位置，部署前应核对这一接口变更。仍不注册通用 DOM 或数据库工具。
输出发布清单包含地图/导航模块，以满足 `ai-control.js` 的静态依赖；源码更新不代表已部署。
导航更新没有新增运行依赖，可复用已验证的 outputs 隔离解释器；从 Git 提交导出快照发布。
不迁移数据库，不自动重试或回滚；旧 release/解释器/配置用于人工恢复。

当前入口 https://47.239.50.129/prs/live/ 。`/prs/` 转到同一页面，仅 Echo 和最新 HF 视频路径。
视频链路调优使用 `--profile live-tuning --runtime <当前服务实际解释器目录>` 暂存，
再用对应清单 `--activate`。它只覆盖 camera/pipeline/client、PRS/HF transport 及对应测试，
要求模型清单和推理计划与当前服务器逐字节相同；复制 current 后保留语音/振动、地图、
MCP registry、systemd/nginx/env、GPU计算实现和运行依赖。激活只切换源码并重启 PRS。
HF 与 PRS 的窗口和启动控制契约必须协调发布；先用 CPU health 确认 HF 新 SHA 已就绪，
再激活 PRS，最后核对公网资产哈希。健康请求不替代真实 GPU 性能测量。
`warmup_bytes=524288` 表示首次结果前一次同连接双向预热，额外流量为1MiB，
额外耗时计入首帧；3秒控制截止、5秒worker空闲和55秒会话/冷等待预算分别保留。
直接连接 HF 的工具通过 `receive_live_result` 确认控制；浏览器仅收到原检测结果。
手动震动 UI 可显式使用 `--profile vibration --source-root <已审查的Git快照目录> --git-commit <SHA>`
暂存。此配置只覆盖震动页面、样式、共享词典和Python资源白名单及对应文档/测试，
要求快照的模型清单和推理计划与当前服务器逐字节一致。先从current复制新release，
保留摄像头/推理、地图、ASGI及运行依赖，不删除旧路径、不覆盖systemd/nginx模板或token。
激活时校验暂存哈希和current仍指向原release，再切换链接并仅重启prs-camera、检查健康；
不reload nginx、不自动重试或回滚。发布清单记录Git SHA，原release保留供人工恢复。
公开UI回归使用前端 `tools/check_vibration_browser.mjs`，其原生震动API是明确替身，
不代表真实手机马达验证；实际震动仍需安卓手机前台手动测试。
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
TasksMax32；WebSocket 队列5、帧1MiB、无压缩。Nginx 视频 WS 读超时65秒、MCP70秒，其他 PRS 路径30秒，
关闭上游重试及缓冲，清除 Cookie/Authorization/Forwarded；公网 admin 返回404。

HF 源码发布和旧接口删除见 [ZEROGPU.md](../ZEROGPU.md)，当前测试及历史长尾见
[PHONE-LIVE.md](../PHONE-LIVE.md)。
