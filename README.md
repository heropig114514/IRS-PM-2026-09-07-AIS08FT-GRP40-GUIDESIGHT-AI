# PRS Practice Module

前后端开发仓库。后端沿用 AiInterviewer 的 Django + DRF + ASGI 技术栈及分层思路，独立维护业务与数据库；前端使用原生浏览器 API。

```text
backend/              后端唯一开发入口
  config/             开发/生产配置与 ASGI/WSGI 入口
  core/               公共接口、错误处理及框架测试
  deploy/             独立摄像头服务、Nginx 路由和运维说明
  requirements*.txt   固定直接依赖
  README.md           安装、启动、检查与扩展说明
frontend/             摄像头回传、Google Maps 设备定位与客户端测试
.github/workflows/    后端自动检查
```

当前实现手机摄像头 JPEG 帧经 WebSocket 原样回传，校验字节后显示；另提供应用级 ping/pong。图像只在内存中处理，不录制、不存储。最大 4 个连接、单帧 1 MiB，无自动重连或重试。

服务器入口：[摄像头回传](https://47.239.50.129/prs/)。开发启动与检查见 [后端说明](backend/README.md)，协议与操作见 [前端说明](frontend/README.md)，隔离部署见 [运维说明](backend/deploy/README.md)。当前未实现模型推理、持久化媒体或业务账号接口。

[Google Maps 定位页面](https://47.239.50.129/prs/maps/) 由用户点击请求设备位置，显示经纬度、精度和地图。需在本应用环境配置 `GOOGLE_MAPS_API_KEY`；未配置时明确显示缺失，不能据此宣称 Google API 已验证。位置不上传至 PRS 服务端；Google 地图请求使用浏览器 key 和设备坐标。

本地已有课程文件、报告和压缩包不纳入 Git；`.gitignore` 使用根目录白名单。未来新增需要提交的根目录路径时，须显式加入白名单。
