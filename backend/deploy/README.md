# 摄像头回传独立部署

目标服务器 Ubuntu 22.04，访问入口 `https://47.239.50.129/prs/`。新增路由只占 `/prs`，保留原 `/` 应用。部署前须确认目录、服务名称及端口未被使用，不覆盖其他项目的文件或数据库。

## 隔离边界

- 源码：`/opt/prs-practice/releases/<Git SHA>`；`current` 指向当前发布。
- Python：`/opt/prs-practice/python` 的独立解释器副本和 `/opt/prs-practice/venv`；不修改系统或旧应用环境。
- 身份：`prs-camera` 非登录系统用户；无 root 运行的应用进程。
- 环境：`/etc/prs-camera/app.env`，root 0600；不能提交仓库或写入日志。
- 数据：`/var/lib/prs-practice/db.sqlite3`，本应用专用 SQLite，只保存 Django 系统表，不存摄像头图像。
- 服务：`prs-camera.service`，单 worker、`127.0.0.1:8766`，256 MiB 内存、20% CPU、32 个任务上限；无自动重启。
- TLS：沿用现有有效 IP 证书和 Nginx 443 入口，不改证书续期任务。
- Nginx：只向原 HTTPS server 增加 `include /etc/nginx/prs-camera.locations.conf;`；该文件来自本目录模板。改动前保存原配置，`nginx -t` 通过后只进行 graceful reload，不 restart。
- 代理清除 Cookie/Authorization，不将现有应用会话交给诊断服务；`/prs/admin` 不公开。其他路径及旧应用进程保持原装配。

`config.camera_server` 必填环境：

```dotenv
DJANGO_SETTINGS_MODULE=config.camera_server
DJANGO_SECRET_KEY=<独立生成，不沿用旧应用密钥>
CAMERA_PUBLIC_ORIGIN=https://47.239.50.129
PRS_DB_PATH=/var/lib/prs-practice/db.sqlite3
```

## 发布与检查

从已经通过本地检查的 Git 提交生成源码归档，仅上传跟踪文件，排除 .env、数据库、虚拟环境及课程材料。解压到新的 SHA 目录，安装本应用依赖，初始化本应用数据库，再安装本目录的 systemd 单元及 Nginx include。不要对旧应用运行 migrate、pip install 或 restart。

```bash
systemctl status prs-camera --no-pager
journalctl -u prs-camera -n 40 --no-pager
curl -fsS https://47.239.50.129/prs/api/health/
curl -fsS -o /dev/null -w '%{http_code}\n' https://47.239.50.129/login/
```

健康接口检查专用数据库。公网 WebSocket 测试必须同时提供 Origin `https://47.239.50.129`，发送 ping 检查 id，再发送合成 JPEG 核对每个字节；这不等于真实手机摄像头验证。不要把服务端摄像头帧写入文件进行诊断，日志只记录连接标识、数量、字节总数及错误位置。

停止本应用：`systemctl stop prs-camera`。不要停止原 Nginx、PostgreSQL、Redis 或 AiInterviewer。移除代理入口时只移除本项目 include 行，先 `nginx -t` 再 reload，保留原配置备份及服务数据供恢复。证书有效性由原续期系统管理；本项目不单独获取或修改证书。
