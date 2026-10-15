# PRS Practice Module

前后端开发仓库。后端沿用 AiInterviewer 的 Django + DRF + ASGI 技术栈及分层思路，独立维护业务与数据库；前端使用原生浏览器 API。

仓库以 `backend/` 和 `frontend/` 为顶层，按实际克隆路径启动。本机MVP通过独立Web和CUDA推理进程接通已有软件与算法；两个环境、官方权重、启动器和验证边界见 [本地MVP](backend/LOCAL-MVP.md)。

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

页面入口为 `/live/`。原软件提供Echo/HF；本地集成入口额外启用本地CUDA双模型检测，浏览器经同源WebSocket发送JPEG并显示标注结果。实时图像只在内存中处理，不录制、不存储，无自动重连或重试；Echo最多4个连接，检测会话按各自协议限流。

本机入口由 `backend/tools/run_local_mvp.py` 启动，默认 http://127.0.0.1:8766/live/ 。算法复用YOLO26n、YOLO26 Depth、BoT-SORT、距离运动状态及TTC/规则风险；浏览器Guidance在用户启用后仲裁语音/振动提醒与导航示例。中台负责协议和运行环境适配，不修改模型参数或风险规则。工件与许可见 [模型说明](backend/models/README.md)。

独立本地视频入口为 `/integration/video/`，用户选择本地文件后按原生1倍速回放，不申请电脑摄像头权限。它复用相同双模型与提醒链路，保留原参数及55秒会话预算；当前选用MOT17白天/夜间移动街景样本，来源及显式转码记录见 [视频数据说明](backend/model_tests/DATASETS.md#连续街景视频用于本地联调)，采集/媒体两个时钟和验收命令见 [连续视频说明](backend/LOCAL-MVP.md#连续本地视频入口)。

MOT17的两个原始预览、两个H.264回放副本及来源清单属于仓库测试素材；完整数据集、标注包和运行输出按目录规则单独管理。验收应区分吞吐、采集到告警的时延和设备实际输出，具体方法见 [本地验收说明](backend/LOCAL-MVP.md#验收方法与边界)。

开发启动与检查见 [后端说明](backend/README.md)，协议与操作见 [前端说明](frontend/README.md)，隔离部署见 [运维说明](backend/deploy/README.md)。当前未实现持久化媒体或业务账号接口。

可选 [Hugging Face ZeroGPU Space](https://huggingface.co/spaces/Heropig114514/prs-yolo26) 模式需服务端token及足够免费额度，一次视频最多55秒，首帧/后续等待预算55/10秒。配置及额度边界见 [ZeroGPU说明](backend/ZEROGPU.md)；本地MVP启动器明确关闭外部凭据。

当前提供原样回传、本地CUDA集成、HF GPU检测、可选场景描述和定位/步行路线。模型导出、Space发布及验证工具集中于 `backend/tools/`；结构化实验结果存放于 `backend/benchmarks/`。

[步行路线测试页面](https://47.239.50.129/prs/maps/) 使用 Leaflet 1.9.4 + OpenStreetMap 底图和 ORS `foot-walking` 路线。服务端配置 `ORS_API_KEY`，浏览器仅获配置状态。点击获取设备起点，点选地图终点，再提交路线；也可明确选择 NUS 固定示例。规划时起终点发送 PRS 和 ORS，不存数据库；返回路线、距离、预计时间和转向步骤。初始 NUS 区域预览和示例均不是设备位置。

路线规划使用ORS，持续定位和偏航判断尚未实现。本地Guidance的导航示例用于提醒仲裁，不代表GPS/ORS或完整导航验证；真实手机声音、振动和风险准确率需另行验证。

本地已有课程文件、报告和压缩包不纳入 Git；`.gitignore` 使用根目录白名单。未来新增需要提交的根目录路径时，须显式加入白名单。
