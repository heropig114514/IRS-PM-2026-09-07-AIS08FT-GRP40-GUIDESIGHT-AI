# PRS Practice Module

前后端开发仓库。后端复用 AiInterviewer 的 Django + DRF + ASGI 技术栈及分层思路，独立维护业务与数据库，不依赖面试系统源码或模型。

```text
backend/              后端唯一开发入口
  config/             开发/生产配置与 ASGI/WSGI 入口
  core/               公共接口、错误处理及框架测试
  requirements*.txt   固定直接依赖
  README.md           安装、启动、检查与扩展说明
frontend/             前端开发预留目录
.github/workflows/    后端自动检查
```

快速开始见 [后端说明](backend/README.md)。健康检查：`GET /api/health/`；管理后台：`/admin/`。

当前仅为可运行框架，尚未实现项目业务、模型推理、文件上传或实时任务协议。前端也尚未选择组件框架。

本地已有课程文件、报告和压缩包不纳入 Git；`.gitignore` 使用根目录白名单。未来新增需要提交的根目录路径时，须显式加入白名单。
