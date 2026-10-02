# PRS Practice Module

前后端开发仓库。后端沿用 AiInterviewer 的 Django + DRF + ASGI 技术栈及分层思路，独立维护业务与数据库；前端使用原生浏览器 API。

本机开发目录已独立整理为 `D:\my_files\NUS_teamwork\PRS-PracticeModule\development`，请在编辑器中打开此目录；后端位于其 `backend/`。Git 仓库和隔离运行环境随代码迁移，依赖版本保持不变。课程与个人资料保留于外层 `PRS-PracticeModule`。GitHub 仓库内部仍以 `backend/` 和 `frontend/` 为顶层；克隆到其它机器时按实际仓库路径启动。

```text
backend/              后端唯一开发入口
  config/             开发/生产配置与 ASGI/WSGI 入口
  core/               公共接口、错误处理及框架测试
  deploy/             独立摄像头服务、Nginx 路由和运维说明
  requirements*.txt   固定直接依赖
  README.md           安装、启动、检查与扩展说明
frontend/             摄像头回传、Leaflet/OSM 步行路线与客户端测试
.github/workflows/    后端自动检查
```

当前实现手机摄像头 JPEG 帧经 WebSocket 原样回传，校验字节后显示；另提供应用级 ping/pong。图像只在内存中处理，不录制、不存储。最大 4 个连接、单帧 1 MiB，无自动重连或重试。

服务器入口：[摄像头回传](https://47.239.50.129/prs/)。新增可选 YOLO26n COCO 80 类预训练检测：服务端在返回 JPEG 绘制框、类别和置信度，无需额外训练。页面保留默认原样回传，另可点击“测试示例识别”验证公开样例。检测参数、资源测试和范围见 [检测说明](backend/VISION.md)，工件与许可见 [模型说明](backend/models/README.md)。

开发启动与检查见 [后端说明](backend/README.md)，协议与操作见 [前端说明](frontend/README.md)，隔离部署见 [运维说明](backend/deploy/README.md)。当前未实现持久化媒体或业务账号接口。

可选 [Hugging Face ZeroGPU Space](https://huggingface.co/spaces/Heropig114514/prs-yolo26) 已部署并返回标注样例。页面可明确选择该模式，经云服务器转发；只有该模式最多等待60秒，现有推理模式保持原预算。免费配额与服务器 token 配置、实测范围见 [ZeroGPU 说明](backend/ZEROGPU.md)。

当前只保留原样回传、服务器 CPU 检测、HF GPU 检测和定位/步行路线。模型导出、Space 发布及验证工具集中于 `backend/tools/`；历史测试记录保留于 `backend/benchmarks/`。

[步行路线测试页面](https://47.239.50.129/prs/maps/) 使用 Leaflet 1.9.4 + OpenStreetMap 底图和 ORS `foot-walking` 路线。服务端配置 `ORS_API_KEY`，浏览器仅获配置状态。点击获取设备起点，点选地图终点，再提交路线；也可明确选择 NUS 固定示例。规划时起终点发送 PRS 和 ORS，不存数据库；返回路线、距离、预计时间和转向步骤。初始 NUS 区域预览和示例均不是设备位置。

按用户选择以 ORS 替代原 proposal 中的 Google 路线实现；原 proposal 文件未改写。当前只完成路线规划原型，持续定位、偏航判断、Guidance Manager 与 TTS/避障调度待开发。后续正式实验需明确记录导航实现与地图数据来源。

本地已有课程文件、报告和压缩包不纳入 Git；`.gitignore` 使用根目录白名单。未来新增需要提交的根目录路径时，须显式加入白名单。
