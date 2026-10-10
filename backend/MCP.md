# PRS 的 AI 操作接口

系统使用 [官方 MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) 2.3.0，提供
Streamable HTTP MCP 服务。它与 Django、摄像头和地图共用一个 ASGI 进程和数据库。
当前按需求不增加 MCP 鉴权或 SQL 权限限制。
上述完整接口使用默认 `PRS_MCP_PROFILE=full`。本次公网设备输出发布显式配置
`PRS_MCP_PROFILE=outputs`，只注册 `ui_sessions` 和震动/语音的七项输出工具。
其余函数保留供本地完整配置使用，不注册到该公网工具目录；无效配置停止启动，不回退。
公网入口为 `https://47.239.50.129/prs/mcp`，使用相同的 Streamable HTTP 客户端协议。
在摄像头页真实点击并启用输出后，调用会话发现选择该 tab；公网输出配置不提供自动启用开关的工具。

## 启动和连接

在 `backend` 执行，已有 `.env` 中的 `DJANGO_SECRET_KEY` 和数据库配置继续使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --workers 1 --lifespan on --ws websockets-sansio --ws-max-size 1310728 --ws-per-message-deflate false
```

给支持 Streamable HTTP 的 AI/MCP 客户端配置地址：

```text
http://127.0.0.1:8766/mcp
```

使用 `mcpServers` 配置格式的 HTTP 客户端可以参考：

```json
{
  "mcpServers": {
    "prs": {
      "type": "http",
      "url": "http://127.0.0.1:8766/mcp"
    }
  }
}
```

不同客户端的配置文件格式可能不同，服务地址和传输类型相同。本次没有修改任何个人 AI 客户端配置。
客户端在别的设备上时，需要能访问服务所在机器，`127.0.0.1` 指向客户端自身。

在浏览器打开 `http://127.0.0.1:8766/live/` 或 `/maps/`。两个页面自动注册 AI 控制连接。
AI 先调用 `ui_sessions`，选择返回的 `session_id`，再调用 `ui_snapshot` 读取页面和可用控件。
AI 控制的是这个已打开的页面，不需要单独安装浏览器扩展。

必须启用 ASGI lifespan，并使用一个 worker。Django `runserver` 仍仅用于独立的本地管理后台，
不提供 MCP 或 WebSocket。多个 worker 会把浏览器连接和工具请求分散到不同进程。

## 工具

| 工具 | 操作 |
|---|---|
| `system_status` | 实际数据库探测、已连接页面、ORS/HF 配置存在状态 |
| `system_routes` | 发现 Django 路由、页面目的地和 ASGI 路径 |
| `system_request` | 调用既有 HTTP 接口，保留中间件、校验和 HTTP 状态 |
| `ui_sessions` | 列出已打开并连接的 PRS 页面 |
| `ui_snapshot` | 读取当前 URL、文字、状态、控件选择器、值、选项和坐标 |
| `ui_navigate` | 跳转到 `camera` 或 `maps`，等待新页面连接后返回快照 |
| `ui_action` | 点击、填值、选择、勾选、键盘事件、提交、滚动、等待状态 |
| `vibration_status` | 读取指定摄像头页的震动支持、启用开关、可见性和用户激活状态 |
| `vibration_test` | 调用一次短震、长震或双震，返回浏览器接受/拒绝及原因 |
| `vibration_stop` | 调用一次取消震动，不要求启用开关仍开启 |
| `speech_status` | 查询语音输出前提、排队/朗读/完成/停止/错误及最近请求元数据 |
| `speech_voices` | 查询设备语音ID、语言、本地/远程服务与默认标记 |
| `speech_speak` | 提交一次受控文本朗读，不自动排队或替换指定声音 |
| `speech_stop` | 调用一次取消当前页面的语音输出 |
| `database_schema` | 实际数据库表、字段和 Django 模型结构 |
| `database_query` | ORM 查询、Django 条件表达式、排序和分页 |
| `database_write` | ORM 创建、更新、删除，返回受影响数量 |
| `database_sql` | 执行一条 SQL，支持读写和 DDL、绑定参数 |

页面上的语言切换、摄像头选择、Echo/HF 模式、连接测试、开始/停止、设备定位、示例坐标、
地图终点和路线提交均可通过这些控件触发。原有路线服务、HF 推理条件、图像参数和错误处理不变。
新增后端 HTTP 路由会自动出现在路由发现结果中；新增页面若需要 DOM 控制，应加载
`ai-control.js` 并正确设置 `data-api-base` 为同源应用根目录的相对路径。

## 操作示例

以下 JSON 是工具参数；`session_id` 替换为 `ui_sessions` 返回值。

震动调用必须选择已打开的摄像头页。用户先在该页真实点击并启用“震动测试”，
随后 AI 可以调用以下工具；MCP 不会自动启用开关、制造可信用户激活或重复调用。

```text
vibration_status({"session_id":"..."})
vibration_test({"session_id":"...","pattern":"double"})
vibration_stop({"session_id":"..."})
```

`pattern` 只接受 `short`（200ms）、`long`（800ms）、`double`（150ms/暂停100ms/150ms），
省略时默认为 `short`。三者均复用前端按钮的 `VibrationTester`，不执行检测或申请摄像头权限。
查询返回 `available/supported/enabled/visible/user_activated/request_pending/hardware_verified`；
浏览器未提供 User Activation 信息时 `user_activated` 为 null。
测试/停止另返回 `operation/accepted/reason`，测试还包含 `pattern`。
`request_pending` 只是已接受且未成功取消的请求记录，不表示马达仍在运行。
`accepted: true` 仅为浏览器接口接受，`hardware_verified` 始终为 false。

设备前提不足或原生接口失败会返回明确的 `accepted: false`，不依赖中英文显示文字：

| `reason` | 含义 |
|---|---|
| `page_unavailable` | 所选页面没有震动 UI，请明确选择或导航到摄像头页 |
| `unsupported` | 浏览器没有震动接口 |
| `disabled` | 用户尚未启用测试 |
| `page_hidden` | 测试页面不在前台 |
| `activation_required` | 浏览器尚未记录真实用户交互 |
| `native_rejected` | 原生接口返回 false |
| `native_error` | 原生调用抛出异常，详情已记录于浏览器控制台 |

无效参数、未连接会话和传输超时/断开仍为 MCP 错误；超时后的动作完成状态未知，
必须先查询页面状态，不能自动重放。停止可以独立于开关发送取消请求，但浏览器仍可能拒绝。
安卓 Chrome 等可测试；iPhone Safari 不支持该接口。[浏览器限制说明](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/vibrate)

真实 HTTP/WS/Chrome 联调命令见前端说明。测试明确替换原生震动 API，不代表实际手机马达验证。

语音输出同样需要用户在摄像头页真实点击并启用开关。浏览器共用接口为
`frontend/speech-output.js` 导出的 `speechOutput`（其他页面为null）：
`describe()`、`voiceList()`、`speak(text, options)`、`stop()`。
UI按钮、浏览器控制桥和以下MCP工具使用这一实例，不申请麦克风/摄像头权限。

```text
speech_status({"session_id":"..."})
speech_voices({"session_id":"..."})
speech_speak({"session_id":"...","text":"你好，这是语音输出测试。","language":"zh-CN","rate":1.0,"volume":0.8})
speech_stop({"session_id":"..."})
```

`text`必须有非空白内容且最多1000个Unicode码点，保持原文不改写。
`language`只接受`zh-CN/en-US`（默认`zh-CN`）；`rate`为0.5至2（默认1），
`volume`为0至1（默认1，0为明确静音）。省略/空`voice_id`明确使用浏览器默认声音，
也可使用`speech_voices`返回的精确ID，最多1024个字符。指定ID失效、歧义、查询失败或
主语言不匹配均明确拒绝，不改用默认声音；语音列表可能通过`voiceschanged`稍后加载。
语音条目的`local_service`来自浏览器，false表示远程服务，可能联网；默认声音是否本地
在提交前未必可知，不声称所有语音离线。[语音服务属性](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice/localService)

提交成功只说明构造请求并调用`speechSynthesis.speak()`未同步抛出异常，
不是已开始或已播放完成。必须继续读取`state`：`queued/speaking/completed/stopped/error`；
初始为`idle`，`start/end/error`事件更新引擎状态。
`audio_output_verified`始终false；即使引擎报告完成，实际声音也受设备/系统音量影响。
接口返回请求ID、Unicode长度、语言、声音ID、语速和音量等元数据，不回显朗读正文，
应用日志也不记录正文。原生错误只回显标准码，未知外部内容规范为`unknown`。

停止发送一次`cancel()`，成功后隔离旧请求迟到事件；取消异常保留当前请求供诊断，
返回`cancel_failed`。关闭开关、页面隐藏/离开时取消当前输出，回前台不自动恢复。
已有请求或原生引擎繁忙时返回`busy`，不自动排队、打断或重试。
其余明确原因包括`page_unavailable/unsupported/disabled/page_hidden/activation_required`、
`invalid_text/invalid_parameters/voice_query_failed/voice_unavailable/voice_ambiguous/voice_language_mismatch/native_error`。
MCP参数违反类型/范围、无会话或传输失败仍是工具错误；异步合成错误见`last_error`。
此接口朗读当前浏览器设备，不生成MP3/WAV文件，不绑定检测结果。
[输出及事件语义](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis)

```json
{"session_id":"...","page":"maps"}
```

上面调用 `ui_navigate`。在地图初始化完成前用 `ui_action` 等待示例按钮可用：

```json
{"session_id":"...","action":"wait","selector":"#example","state":"enabled","timeout":20}
```

然后使用相同工具点击示例、填写终点、提交表单：

```json
{"session_id":"...","action":"click","selector":"#example"}
{"session_id":"...","action":"fill","selector":"#end-longitude","value":"103.7735"}
{"session_id":"...","action":"submit","selector":"#route-form"}
```

地图选点可以使用 `click` 的 `x/y`，它们是元素左上角起算的像素偏移。
`scroll` 将控件滚动到视野中央；`check` 接受布尔值；`select/fill/key` 接受字符串。
`key` 发送 DOM 键盘事件，不执行浏览器默认打字或系统快捷键；输入文字使用 `fill`，
提交使用 `submit`。不唯一的选择器、隐藏/禁用控件、无效选项会明确失败。

点击成功表示事件已触发，摄像头或路线请求可能还在执行。继续调用 `wait` 或 `ui_snapshot`
观察完成状态，不能仅凭点击响应断言业务成功。快照文字最多 30000 字符、控件最多 500 个，
超过时返回明确的截断标志。快照不包含摄像头图像像素。

调用 `system_request` 检查原有后端：

```json
{"path":"/api/health/"}
{"path":"/maps/route/","method":"POST","body":{"start":[103.7766,1.2971],"end":[103.7735,1.2991]}}
```

接口路径不带部署前缀 `/prs`。默认 Host/Origin 使用系统配置的页面源。
接口响应保留原始 HTTP 状态，不重试或跟随重定向；既有认证接口可明确提供所需请求头。

先通过 `database_schema` 获取模型标签和字段，再进行 ORM 操作：

```json
{"model":"auth.Group","fields":["id","name"],"limit":20}
{"action":"create","model":"auth.Group","values":{"name":"example-group"}}
{"action":"update","model":"auth.Group","filters":{"name":"example-group"},"values":{"name":"renamed-group"}}
{"action":"delete","model":"auth.Group","filters":{"name":"renamed-group"}}
```

更新/删除要求显式传 `filters`；`{}` 表示匹配全部。外键使用 schema 中的 `attname`
（例如 `user_id`）。创建/更新执行 `full_clean` 和 `save`，整批操作失败会回滚。
删除沿用 Django 的级联规则。密码字段不会自动哈希；这不是账号注册接口。
当前没有产品业务模型，ORM 工具操作已有 Django 模型，SQL 可以操作实际表。

SQL 参数使用 Django 的 `%s` 占位符，不自行拼接输入：

```json
{"sql":"SELECT id, name FROM auth_group WHERE id >= %s ORDER BY id","parameters":[1],"limit":20}
```

SQL 和 ORM 查询返回量最多 1000 行；SQL 的 `limit` 只限制返回量，不限制写入数量。
SQL 不附加权限过滤，可以执行 INSERT/UPDATE/DELETE/DDL，实际支持范围由数据库决定。
数据库失败返回异常类型、必要的校验字段/代码，不在日志中输出 SQL、数据值或凭据。

## 连接及运行边界

浏览器必须保持页面打开。跳转保留 tab 的 sessionStorage ID；新连接替换同 ID 的旧连接。
复制 tab 时浏览器可能复制 sessionStorage，出现相同 ID 时以最新连接为准；独立新 tab
使用自己的 ID。关闭或刷新会终止未完成的控制请求。断线后手动刷新，不自动重连。
浏览器控制通道每 15 秒发送心跳，避免空闲 Nginx 连接断开。

摄像头、定位仍需要浏览器权限和实际可用设备。MCP 的 DOM 点击不能授予浏览器权限，
不能产生系统认可的可信用户手势，也不提供文件选择弹窗、浏览器外系统快捷键或其他网站控制。
该实现覆盖当前 PRS 页面和后端功能；这些浏览器/设备边界不能称为已消除。
标准工具超时、断开和浏览器动作错误会明确返回；超时后动作可能已经执行，应先查看状态。
服务没有自动重试、隐式更换镜头/推理模式或自动数据库写入重放。

## 部署与验证

生产模板使用 `/prs/mcp`，启用 lifespan。MCP 代理读超时 70 秒，媒体路径原预算保持。
发布工具的 allowlist 包含新后端模块、依赖文件和前端桥接模块；staging 校验依赖及 ASGI 导入。
服务器需事先明确安装 `requirements.txt` 中的新依赖，发布脚本不会隐式修改运行环境。
本次仅完成本地实现与验证，未发布公网、未修改个人客户端配置、未创建 Git 提交。

只读协议验证：

```powershell
.\.venv\Scripts\python.exe -m tools.check_mcp --url http://127.0.0.1:8766/mcp --output benchmarks/2026-10-05/mcp-readonly.json
```

增加 `--exercise-db` 会创建/读写/删除唯一命名的测试 Group；结束后清理自己的测试行。
`--exercise-ui` 用于专用测试 tab，会操作语言、摄像头和地图。
完整 Chrome 联调从 `frontend` 执行，`PYTHON` 指向 backend 的 `.venv/Scripts/python.exe`，
`PRS_PLAYWRIGHT_PATH` 指向已有 Playwright 包：

```powershell
node tools/check_mcp_browser.mjs http://127.0.0.1:8766/live/ benchmarks/2026-10-05/mcp-browser-local-final.json --synthetic-facing
```

Chrome 原生模拟摄像头无法满足 `facingMode.exact=environment`，失败记录保留在
`benchmarks/2026-10-05/mcp-browser-camera-diagnostic.json`；相同启动条件下的原生媒体探测结果
保存在 `mcp-camera-native-probe.json`。
显式 `--synthetic-facing` 仅在测试浏览器中移除这一模拟设备不支持的条件，保留尺寸/audio
约束；实际系统严格镜头条件不变。地图配置/路线也使用明确合成的夹具，真实 DOM、JS、JPEG、
WebSocket、MCP HTTP、数据库及页面跳转均执行；不代表真实摄像头、GPS、ORS 或 GPU验证。

本次完成 Django check、迁移差异检查、100 项后端测试、37 项前端测试、依赖完整性检查、
变更 Python 文件 Ruff 和新 JavaScript 语法检查；真实 Chrome/MCP 联调记录为
`mcp-browser-local-final.json`。另实际验证 2025-06-18 的 initialize/tools/list/tools/call，
发现 11 个工具且数据库探测成功，兼容该协议版本的传统客户端。
仓库没有 `tools/check_docs.py`，已人工核对变更文件的注释与声明/变量索引；未创建提交，
因此未验证 Git 提交原子性。完整 `ruff check .` 仍在既有
`benchmarks/2026-10-04/kaggle-v11/gpu-rebenchmark.ipynb` 报 I001 和 E501，未修改历史实验工件。
公网代理/systemd、PostgreSQL、真实摄像头/GPS及 ORS/HF 上游尚未在本次工作中验证。
