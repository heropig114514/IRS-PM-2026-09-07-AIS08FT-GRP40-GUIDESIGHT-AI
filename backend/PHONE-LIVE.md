# 2026-10-05 当前公网页面短测与浏览器契约修正

当前完整浏览器路径实测 **10.3486 FPS**，尚未达到12FPS；默认输入采样上限仍为12FPS。
本次用真实Chrome页面/canvas JPEG编码/PRS与HF往返/完整性校验/顺序解码短测61帧，
61/61全部交付，首帧14.718秒单列，后续60帧在5.798秒内解码完成；未过滤任何慢帧。
图源是公开公交车图片生成的canvas MediaStream，不包含真实手机传感器、移动网络或屏幕绘制。

| 观察路径 | 完成交付 | 首帧后FPS | 最大采集到解码 |
|---|---:|---:|---:|
| 原HF FP32，公网原字节输入/返回，2026-10-03 | 4＋180全部 | 12.001 | 不包含浏览器采集/解码 |
| 原HF FP32，完整Chrome页面，2026-10-03 | 241/241 | 10.707 | 443.8ms |
| 当前HF FP16 compiled＋Graph，完整Chrome页面，本次短测 | 61/61 | 10.349 | 557.0ms |

上述观察的时段、HF及网络状态不同，不用于计算端到端加速比。12.001FPS记录不能直接代表
包含浏览器采集/解码的同等吞吐。进一步对照发送实现和逐帧时间戳，原字节脚本按
`started + ordinal/fps`绝对时间点发送已编码JPEG，实际输入12.008FPS，平均间隔83.276ms；
浏览器按实际采集时刻安排下一帧，停顿后不补发。本次平均采集间隔93.108ms，实际输入
10.740FPS；历史241帧浏览器的实际输入也仅10.803FPS。调度源码SHA与历史部署完全相同。
第6→7帧间隔175.4ms：预定下一次采集时五个信用均被占用，最早前序解码还需91.667ms。
其余普通采样的具体调度迟延来源未单独记录，不能断言全部由网络或浏览器计时器造成。

本次FPS分母5.7979秒由0.6ms开始间隔、5.4934秒采集跨度、末帧303.9ms返回/解码组成，
末帧等待在短测中的占比更大。按相同前61个连续已保存帧进行诊断，历史浏览器为10.464FPS，
当前10.349FPS；原241帧的官方10.707FPS及所有原始记录保持，不用截段结果替代验收。
这些证据支持测试路径、采样节奏和短窗口影响，不能证明模型优化造成12→10FPS的性能退化。
详细计算、源码身份与限制保存为`hf-jit-browser-pacing-analysis.json`，本次分析未调用GPU。

当前纯CUDA event forward中位数0.73736ms，GPU阶段
中位数2.408ms，服务处理中位数15.436ms，PRS↔HF区间中位数248.722ms。
最慢帧557ms中，PRS↔HF497.664ms、服务处理15.304ms、forward0.734ms、解码2.9ms；
较大等待位于计算之外，尚不能区分该区间内网络、排队和进程交接的具体贡献。
采样设置没有改变，Graph未重复捕获，所有稳态raw/JPEG哈希稳定；未丢帧、隐式重试或降级。
原验收还要求最大延迟≤500ms，本次虽全部交付且≥10FPS，仍明确记录`meets_target=false`。
这一短观察不证明长会话能持续12FPS，也不改变Kaggle纯推理方法排名或COCO质量结论。

首个页面尝试在发送首帧前报“视频会话配置无效”：浏览器后端白名单遗漏已部署的
`PyTorch compiled CUDA`。此前两个8帧HF直接推理检查未覆盖该浏览器契约，留下了接口缺口。
原失败报告、PRS零输入/零GPU日志和未减少的额度均保留。修正仅增加明确的已部署后端名称，
并用真实`models/inference-plan.json`回归验证准入及每帧身份匹配；质量/输出检查均保持。
当前PRS release为`phone-live-61004595d4ff8c1a`，此前`phone-live-ed296ac59e1be232`保留；
发布清单对比只有客户端及对应测试两个文件变化。公开客户端SHA与本地逐字节一致。
HF提交仍为`eb063ebcccc4461a77c38341456e3dbf22aed962`，无需重启HF或重编译目标缓存。

本次使用一次免费GPU调用，额度减少19.904338秒；剩余143.937195秒、0次免费调用，
调用次数预计于2026-10-05 21:42:40（UTC+8）恢复，恢复前额度门禁会拒绝新HF视频。
会话结束后HF CPU健康为ready且active_session=false。没有继续进行HF长实验。

原始证据均在`benchmarks/2026-10-04/`：`hf-jit-browser-61frames.json`保存全部逐帧计时；
`hf-jit-browser-admission-failed.json`保留修正前失败；`hf-jit-browser-comparison.json`保存
统计口径、区间分解及验证限制；`phone-jit-browser-relay-log.txt`独立确认61输入/转发/返回/worker帧；
`phone-jit-browser-fix-deployment.json`与`phone-jit-browser-public-source.json`保存发布和公开源码身份。

最新HF模型计时另汇总至`core-data-tables/hf-inference-observations.xlsx`：两个独立分配检查
各8帧的pure-forward中位数0.70288/0.70038ms，当前浏览器60个稳态帧0.73736ms；
最新同步H2D/Graph/D2H推理中位数2.320224ms。表保留当前与历史五份报告的109个原始帧，
初始化另列，来源SHA及复核记录保存在同名JSON。旧FP32报告缺少同口径pure-forward和
实际GPU身份，HF相对baseline的加速倍数仍为未测定；Kaggle T4的8.574倍不能套用到HF。
本次仅从已有数据导出，原报告及T4原核心CSV/排名表保持原样，没有新增GPU调用。

前端37项、后端88项测试通过；Django check、迁移dry-run、服务器检查、Nginx及服务就绪通过。
完整Ruff仍报告保留的Kaggle v11 Notebook两项原格式问题（import空行、124字符行）；未修改原提交。
仓库没有`tools/check_docs.py`，两个变更代码文件的注释、声明/变量索引人工复核；未做Git提交。

# 2026-10-05 T4最优纯推理配置部署（原迁移记录）

当前推理配置来自同一Kaggle Notebook v8的重复最优方案：FP16、Conv/BN融合、
channels-last、torch.compile(default/fullgraph/static)、CUDA Graph及锁页缓冲复用。
原模型权重/640/阈值0.25/JPEG80不变，公开数组为FP32。T4纯forward组中位数1.742272ms，
同版公平baseline14.938976ms，约8.574倍；三个完整5000图COCO AP均39.156308%，
对baseline下降0.014207个百分点，满足批准的0.5个百分点质量门槛。

HF已发布修正版提交eb063ebcccc4461a77c38341456e3dbf22aed962，19个发布源码Git blob
身份已核对。实际GPU仍为Blackwell MIG2g.48gb；startup在目标GPU编译40.398秒，
保留自身compiler artifacts及Inductor/Triton缓存目录，没有迁移T4二进制或更换免费硬件。
缓存SHA为4c1a0062c0d54563a9288f20f454d6f27058c6dd7ebccb6511437f9751eed62d。

两次独立GPU分配均8/8帧通过，PID1376被HF复用；每次显式重置Dynamo前端状态后恢复
磁盘缓存，FX hit=1且无FX miss/bypass/disabled，新后端代码生成被禁止，后续重编译报错。
两次加载8.507/4.375秒，Graph准备0.548/0.536秒，纯CUDA event中位数0.70288/0.70038ms。
各分配只捕获一次Graph，跨分配raw及渲染JPEG哈希一致；四图各13/13参考框匹配。
这些短观察用于部署/复用验证，不作为HF方法排名或公网速度承诺；HF未重做完整COCO。

初版f72689d2的首调用成功，第二调用在同PID绕过FX查找后返回空计数，触发严格检查。
原失败报告和日志完整保留；修正没有放宽缓存命中/输出检查，也没有增加回退或隐式重试。
Kaggle v11两个全新进程各20帧与v8原始预测一致；v12补充同PID重复恢复的实现诊断。
PRS激活身份、两次短调用、源码身份、缓存合同及免费额度见
`benchmarks/2026-10-04/hf-jit-migration-contract.json`及`phone-jit-deployment.json`。
原迁移阶段未进行HF长视频或全链路性能实验；后续一次浏览器短测及契约修正见本文开头。
以下是此前AOT部署和公网测试的历史记录。

# 2026-10-04 加速方案迁移（历史）

Kaggle同一Notebook v10已完成，部署计划为FP16/串行AOT/融合/锁页缓冲/CUDA Graph；
三次完整COCO AP39.1569%，对公平baseline下降0.0136个百分点。HF已发布提交
78f5bf06b040d47d08f7a0e53ff067577d8e588b，PRS已激活release
phone-live-1c35eaf5cef2a6cc；18个HF发布文件的Git blob身份匹配固定提交，来源及部署
日志分别保存在hf-published-source-verification.json与phone-qualified-deployment.json。

HF实际GPU为NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb，Torch2.8/cu128。
原免费zero-a10g硬件配置未改变，没有申请付费硬件；平台标签不等于实际GPU型号。
AOT在目标GPU startup重新编译，未使用T4二进制：编译45.748秒，首次分配加载1.186秒、
Graph准备1.847秒分别记录；包SHA为9f2ff3bc942e5cd99554affd536e95d98f8cd4b68fc164ffeea2d1ec2339752e。
每次分配核对硬件、运行时、配置和包，记录加载/捕获/清理上下文。公开数组仍为FP32，
实际卷积计算为FP16；固定权重、640、阈值0.25及JPEG80不变。

| 验证路径 | 交付 | 首帧后FPS | 最大延迟 | 结果 |
|---|---:|---:|---:|---|
| 原HF FP32，公网Chrome16帧 | 16/16 | 8.866 | 解码553.9ms | 原始对照保留 |
| 新协议，本地RTX4060 FP16 Graph | 16/16 | 11.909 | 解码44.6ms | 本地控制通过，不加载AOT |
| HF AOT，公网Chrome16帧 | 16/16 | 9.772 | 解码447.9ms | 全交付；吞吐未到10FPS |
| HF AOT，四图诊断＋12帧连续输入 | 16/16 | 4.029 | 返回1634.3ms | 全交付；公网目标未通过 |
| HF AOT，公网Chrome55秒自然结束 | 566/571 | 10.992 | 解码565.5ms | 5帧未返回；完整验收未通过 |

两个独立HF调用均16/16交付，worker确认处理数一致；四图13/13参考框按IoU0.5匹配，
平均置信度绝对差0.001051。该四图诊断不替代完整COCO；HF未重做5000图AP。
Chrome使用公开图片的canvas MediaStream，不是手机实拍；结果不包含传感器曝光或屏幕绘制。
首帧、所有慢帧、采样停顿及在途数量保留，没有删帧或修改计时来满足目标。

短测HF稳态中位数：GPU推理1.623ms、完整服务处理10.407ms、PRS至HF往返235.003ms。
四图连续输入最慢帧1634.272ms，同帧服务处理9.776ms/GPU阶段1.566ms，且reader_pause_ms=0；
证据支持等待主要在传输、队列和交接阶段，尚不能确定其中单一根因。跨公网两次短测
不保证相同GPU或网络状态，不能用8.866→9.772直接证明模型加速比；速度结论来自Kaggle。

55秒测试保留571提交、570转发、566返回和worker566的独立日志；硬截止时5个作业未完成。
代码检查显示，worker的55秒硬截止与客户端持续采样允许在边界留下在途作业。
会话自然结束，CPU health随后active_session=false；所有返回帧哈希稳定，Graph未重复捕获。
现有55/60秒预算、五信用及失败语义未调整，因此该问题按不完整交付报告，不宣称已修复。

原始证据：hf-browser-qualified-aoti-16frames.json、hf-qualified-four-fixtures-and-12frames.json、
hf-browser-qualified-aoti-lifetime.json、prs-final-live-log.txt、hf-live-selected-runtime-log.json、
hf-target-package-contract.json、hf-final-cpu-health.json，均在benchmarks/2026-10-04。
汇总hf-migration-contract.json明确区分模型部署/GPU执行与公网验收。
最后免费额度为218.284 GPU秒/4次调用，未额外重试或更换身份。

以下为原版本的公网观察和实现说明，保留历史长尾。

# 手机视频流水线优化与公网验证（2026-10-03）

新入口为 **https://47.239.50.129/prs/live/**。选择 Hugging Face ZeroGPU 后开启摄像头。
当前 `/prs/` 重定向至同一页面，只保留 Echo 和最新 HF 视频检测。
旧 CPU/HF单帧/前端绘框/20秒会话/分段接口已删除。HF仍使用免费的 `zero-a10g`。
以下性能表是已保存版本的基线；精简后版本验证单独记录。

**结论：吞吐量已在实际部署中达到10FPS以上，但不能保证每帧延迟≤500ms。**
241帧短测全部满足；55秒完整会话曾出现1.10秒长尾。新增分段计时后再测，
已返回帧最大467ms，但这次没有重现此前停顿，不能将未重现当成已修复。
主要等待位于PRS↔HF往返及交接；GPU计算和浏览器解码占比很小。

## 实现与选择依据

手机视频使用同源持久WS，由 `core/hf_video_relay.py` 转发至HF已部署的 `/prs_live/ws`。
每段视频只调用一次 `detect_live`，沿用默认60秒GPU任务，worker最多55秒；停止、断开、
到时或5秒无输入均结束。不自动重连、重试、切换身份、再申请GPU或使用付费硬件。
公开示例识别按钮及对应 HTTP 接口已删除；原图片移至开发测试目录。

`frame-pipeline.js` 将采集/发送和顺序解码显示解耦：首帧单独完成，之后默认12FPS输入上限，
最多5个正在采集、传输或等待显示的作业。空位在采集前取得；停顿后从当前时间继续，
不补发已错过的采样时间，不积压提前采集的旧画面，也不丢弃已提交帧来提高统计速率。
Echo 和 HF 路径均无额外200ms等待。画布尺寸不变时复用，不改变比例或编码参数。

持久连接和有界队列是本项目采用的方案；仅检查浏览器 `bufferedAmount` 不足以表示处理完成，
因为它只计算尚未发到网络的字节。因此浏览器另保留结果验证、排队及解码期间的作业信用。
参考 [MDN bufferedAmount定义](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/bufferedAmount)
及 [websockets有界缓冲说明](https://websockets.readthedocs.io/en/stable/topics/memory.html)。
GPU只在受限函数内复用，函数返回后释放，符合
[HF ZeroGPU分配机制](https://huggingface.co/docs/hub/spaces-zerogpu)。

开始视频前同时检查免费GPU秒数≥60和免费调用次数>0；未知/不足时直接拒绝，不申请GPU。
PRS视频连接独占一个非排队slot，持有至真正的GPU控制任务结束。
HF客户端/worker capability及私有token均不进入浏览器、源码归档或日志。
二进制结果按序验证模型、FP32、80类、原权重SHA、框边界和JPEG SHA256，
实际浏览器解码后再校验尺寸；最多5帧，不使用base64图像。

FP32、模型、最大边640、阈值0.25、采集JPEG0.8及GPU端JPEG80均未改变。
网络/协议发生变化，原始四张图片的输入字节、基线结果及旧探针报告保留用于对照。
部署跨CUDA运行环境的数值结果不宣称逐位相同：13/13框按原IoU0.5标准匹配，
平均置信度绝对差约3.90e-7；同一次新会话内所有稳态raw/JPEG指纹一致。

采集到结果计时从调用capture前开始，到完整包验证结束；采集到解码再包含显示队列和
`image.decode()`。首帧单列；其余慢帧、停顿、排队时间均保留。它们不等于传感器曝光或
屏幕绘制时间。600ms负向测试确认延迟会超过500ms，未重置时间戳或排除慢帧。

结束时按wire顺序完成异步hash，再处理结束和close；GPU控制摘要先完成时仍先排空末尾结果。
自动到时后尚未完成的输入明确保留在发送/返回差值，不计作成功，页面不自动开启下一次分配。
PRS逐帧加入`prs_upstream_ms`：在同一PRS单调时钟上，从提交HF（包含发送等待）计到
完整HF结果验证。它包含HF排队、父进程与GPU交接、处理和网络，不要求两端时钟同步，
仅重新封装元数据，JPEG字节和模型输出保持。

## 证据与边界

| 验证路径 | 成功帧 | 首帧后FPS | 最大采集到结果/解码 | Graph重复初始化 |
|---|---:|---:|---:|---:|
| Chrome→本机PRS relay→原RTX4060→Chrome |121/121|10.834|66.0 /94.0ms|0|
| 原字节四图对照＋公网PRS/HF连续输入 |4＋180全部|12.001|376.49ms（不含浏览器采集/显示）|0|
| Chrome真实页面→公网PRS→HF→Chrome |241/241|10.707|441.3 /443.8ms|0|
| 同路径、55秒自然结束 |570/575|10.619|1095.0 /1097.4ms|0|
| 同路径、新增逐帧PRS计时、自然结束 |577/582|10.767|464.4 /466.9ms|0|

第三行包含真实canvas缩放/JPEG编码、TLS/WS、元数据/完整JPEG校验、顺序排队及浏览器解码。
测试流由公开公交车图片的canvas MediaStream生成，**不是手机实拍**，也不是准确率评估。
第一帧约22.44秒，单列且未计入稳态；后续240帧约22.41秒，P95到结果/解码297.55/300.23ms。
原字节测试15秒180帧，还包含4张原COCO公开对照图、13个原参考框及worker结束确认184帧。
15秒原字节和241帧短测满足原≥10FPS、最大延迟≤500ms条件。
随后完整会话的P95到解码318.68ms，但7个稳态帧超过500ms，峰值1097.4ms；
峰值帧采集9.6ms、传输/交接1085.4ms、GPU阶段2.96ms、服务端处理10.76ms、
解码2.4ms。这排除了该帧纯计算或解码慢的解释；当时没有PRS逐帧计时，
**不能追溯断言停顿究竟来自PRS↔HF还是浏览器↔PRS的具体网络段**。

增加计时后的完整复测P95到解码308.20ms，已返回576个稳态帧均≤500ms。
相同条件均值分解如下（区间有包含关系，不应把GPU列再加进PRS↔HF列）：

| 区间 | 平均耗时 | 含义 |
|---|---:|---|
| 浏览器采集/JPEG编码 |6.60ms|实际页面的capture调用到编码完成|
| PRS提交HF到完整结果验证 |242.47ms|HF往返、排队、进程交接及处理|
| 其中服务端处理 |11.46ms|预处理、GPU和绘框/编码，GPU阶段均值3.05ms|
| 其余传输/验证 |45.25ms|浏览器↔PRS、relay封装及浏览器结果校验，非纯网络|
| 浏览器显示排队＋解码 |2.38ms|不含最终屏幕绘制|
| 采集到解码合计 |296.71ms|全部已完成稳态帧，无慢帧过滤|

本次最大帧466.9ms中，PRS↔HF区间408.45ms、服务端处理11.83ms，
所以本次较大等待主要在该区间的处理之外；不能把这个结论外推成此前1.10秒事件的精确定位。
两次自然结束分别仍有5个已发送输入未完成；HF明确结束，已返回结果已排空，
未完成输入未被补发或计作成功。因此两份整段报告均`complete=false/meets_target=false`，
验收退出1。最后一次虽完成帧的时延达标，仍不能声明整段完整交付。

剩余需要解决的是网络/云端交接长尾；当前没有证据说明再压缩GPU计算能消除这些停顿。
下一步可评估只返回框、浏览器绘制以减少返回字节，但这会改变返回画面契约，
本轮未实施。降低分辨率/精度、丢帧、过滤慢帧或更换付费硬件均未采用。

主要报告在 `benchmarks/2026-10-03/`：

- `phone-browser-native-final.json`：原模型、本机完整浏览器；worker实际确认121帧。
- `phone-relay-deployed-original-inputs.json`：原四图质量/字节对照及公网184帧。
- `phone-browser-deployed.json`：完整公网页面241帧。
- `phone-browser-deployed-natural-end.json`：完整会话，保留1.10秒慢帧和5个未完成输入。
- `phone-browser-deployed-traced.json`：加入PRS计时的完整复测，同样保留5个未完成输入。
- `phone-live-hop-analysis.json`：复测逐帧区间统计；P95使用与浏览器工具相同的线性插值。
- `phone-browser-deployed-traced-startup-failed.json`：发布启动期502，未申请GPU；未覆盖失败报告。
- `phone-live-deployment-traced.json`：性能复测时的18文件哈希、前一版本和配置备份。
- `phone-live-deployment-reviewed.json`：仅修正客户端模块关系注释后的发布，执行语句未改变。
- `phone-live-comment-audit.json`：其余17文件逐字节相同，客户端文件的模块头之后逐字节相同。
- `phone-live-server-final.json`：最终服务状态、源码/公开资产哈希、健康及HF会话释放证据。
- `phone-live-quota-before*.json`：只读免费额度门禁记录，测试后额度另存。
- `phone-live-quota-final.json`：最后一次只读查询，剩余142.54免费GPU秒、4次免费调用；秒数和次数共同约束。

本机测试venv继承系统CUDA，加入项目固定Django/DRF/WS依赖以运行完整页面。
未使用的继承Streamlit/Pillow及lark-oapi/websockets存在版本冲突；不宣称该测试venv的
`pip check`通过。生产沿用原 `runtime-venv` 的固定依赖，不安装CUDA或扩大资源预算。

## 发布与复现

服务器登录信息由用户要求保存在私有 `backend/server_info.txt`，Git明确忽略。
发布工具 `tools.deploy_phone_live` 读取该文件及私有 `.env.hf`，校验已观察SSH主机指纹。
服务器已有额外模式及同名旧HF实现，因此新模块采用独立名称，新页面放在发布的
`frontend/live/`；只合并新增page/WS路由，保留服务器原有实现，未覆盖旧 `hf_live.py/HFSession`。
本地开发根页面使用同一优化源码；独立 `/live/` 挂载由发布工具生成。

发布从服务器current复制至本应用新目录，覆盖18个白名单文件并逐字节校验SHA；
生成修改后的装配、注释/索引及manifest在忽略的 `.space-bundle/phone-live/` 可检查。
没有本地Git提交，因此采用清单发布，不将其它工作区改动或凭据混入源码归档；
不声称已验证单次Git提交原子性。旧current、单元/include/env备份均保留。
只重启PRS，Nginx先检查后平滑reload；256MiB、20%CPU、32任务、单worker及现有TLS保持。
启动期502证明systemd的active早于监听就绪；发布脚本已增加20秒有界本机监听检查，
之后只做一次健康请求，再reload代理。它不重试外部API、请求GPU或自动回滚。
GPU会话结束后CPU健康接口`active_session=false`；PRS进程实测约39–42.3MB，资源限制未扩大。
当前发布为`phone-live-919785e958531fe3`；性能数据来自其上一版本`phone-live-7af1fda36c02237e`，
两者仅模块关系注释不同，未因此额外消耗GPU额度。
新WS独立65秒代理预算，应用首帧55秒/后续10秒是故障预算，不是500ms验收阈值。

本地完成Django check、迁移dry-run、81项后端测试、45项前端测试和Ruff；
注释/声明/变量索引人工复核。仓库没有 `tools/check_docs.py`，没有自动文档检查通过声明。
服务器Django check/迁移dry-run、systemd单元检查、Nginx检查及公网HTTPS页面/健康/原应用入口检查通过。
systemd检查另有原系统snapd的未知RestartMode提示，未改动该服务。原实验GPU模式未逐一复测。

```powershell
# 原字节/模型对照；服务端自行验证免费额度后才调用GPU。
.\.venv\Scripts\python.exe -m tools.benchmark_live_remote --url wss://47.239.50.129/prs/ws/detect/hf/ --origin https://47.239.50.129 --fps 12 --seconds 15 --output benchmarks/2026-10-03/phone-repeat-original.json

# 实际浏览器页面，需本机Chrome和显式测试依赖路径；不得禁用TLS校验。
$env:PRS_PLAYWRIGHT_PATH='<本机Playwright包的绝对路径>'
node ../frontend/tools/benchmark_phone_browser.mjs https://47.239.50.129/prs/live/ 241 benchmarks/2026-10-03/phone-repeat-browser.json

# 自然会话结束；有未完成输入或慢帧仍严格退出失败，不伪造完整交付。
node ../frontend/tools/benchmark_phone_browser.mjs https://47.239.50.129/prs/live/ session benchmarks/2026-10-03/phone-repeat-end.json
```

每次复测都会使用免费额度，须先查询是否足够。本次没有设置定时复测任务。
手机CPU、真实镜头、移动网络和最终显示绘制尚须真机验证。


## 精简路径版本（2026-10-03）

页面只提供 Echo 与最新 HF 视频；根入口重定向到 `/prs/live/`。旧模式的页面项、客户端、
HTTP/WS接口、HF API、服务端实现、专用测试/脚本及配置已删除。保存的完整版本快照保留在
仓库之外 `saved_versions/phone-live-2026-10-03-919785e958531fe3.zip`，基准 JSON 不删除。

模型/图像函数的可执行 AST 与快照相同；CUDA执行程序/模型清单/测试图片/客户端调度和
协议保留对照。`phone-live-minimal-preservation.json` 记录16项实际来源比较。
本机新页面 Chrome＋RTX4060 验证61/61帧、10.902FPS、最大采集到解码56.9ms；只代表
本机及合成图源，不替代公网或真实手机结论。记录为 `phone-browser-minimal-native.json`。

HF精简提交为 `8513c413a3bddca7482ce960220c38a2e0153753`；实际发布/删除列表见
`phone-live-minimal-hf-deployment.json` 与 `phone-live-minimal-hf-deployment-final.json`。
PRS部署、旧接口拒绝、Echo逐字节回传、公网原始四图对照与浏览器重测分别记录到
`phone-live-minimal-deployment.json`、`phone-live-minimal-public.json`、
`phone-relay-minimal-original.json`、`phone-browser-minimal-deployed.json`。
重测保留同一10FPS/500ms指标；历史长尾限制继续成立，短测通过不宣称长尾已解决。

本次不存在 `tools/check_docs.py`，需手动核对改动文件的声明/变量索引及描述与实现。
本机检查包括 Django check、makemigrations --check --dry-run、53项后端测试、36项前端测试、
Ruff及真实浏览器测试。此工作未作本地 Git commit，不宣称本地单commit原子性；HF发布有明确commit。


精简后公网原始四图＋180帧连续检测已完成：四图13/13框按原IoU0.5匹配，180/180稳态帧
全部返回，11.990FPS，P95往返316.1ms，最大350.4ms；没有重复Graph初始化。
此项不包含实际摄像头编码或浏览器解码；完整页面的另一次重测单独记录。
移除无调用者的 eager/clip 辅助函数后，load_model/GraphRunner/export_program 的可执行AST
仍与保存版本一致，不保留可选择的旧推理路径。数据与来源校验见上方 JSON。

## GPU 分配拒绝诊断（2026-10-04，北京时间）

精简版最后一次浏览器调用未进入 GPU worker，原因已通过同一账户、同一 API、原默认
`@spaces.GPU` 参数的一次受控复现确认：HF SSE 返回 `ZeroGPU quota exceeded`，具体为
`90s requested vs. 78s left`。精确余额为77.850766秒，HF错误中的78秒是取整显示；
免费调用仍剩1次。拒绝前后秒数/调用数相同，`active_session=false`，未分配GPU。
原始安全结构化记录见 `benchmarks/2026-10-03/hf-allocation-diagnostic.json`。

本机和PRS服务器token在内存比较一致，账户是非PRO免费个人账户。已排除这次错误是
token不一致、免费调用次数耗尽、并发额度占用或申请时长超过账户上限；没有用其他身份
或付费硬件复现。HF返回的拒绝原文直接确认了实际申请量。

实现缺陷是预检用60秒比较余额，但部署固定的 `spaces==0.51.3` 在 Blackwell 上将默认
60秒乘以配置 `duration_factor=1.5`，实际向调度器申请90秒。包的 `configs.json` 和
`spaces.zero.client.get_duration_seconds` 可核对该计算；此前真实GPU报告的型号为
RTX PRO6000 Blackwell MIG2g.48gb。官方包来自PyPI并校验wheel SHA256后只读解压。
所以77.85秒通过原60秒门禁，仍不足HF的90秒准入要求。

修正仅更新PRS/测试工具的免费额度预检为90秒，并记录所需秒数、错误原因和HF返回的
取整余额。任务默认时长60秒、视频会话55秒、GPU size、模型/FP32/640/0.25/JPEG80及
客户端调度均保持。该90秒是准入预留量，不是每次视频固定扣费，也不是视频运行90秒；
是否放行还要同时检查免费调用次数。预检不能替HF预留余额或保证GPU可用。

错误解析不再仅因文本出现quota就报告额度不足；区分秒数、调用数、并发占用、时长、
Space身份额度、无可用GPU及worker中止，未知错误保留推理失败语义。日志只输出已分类
原因及数字，不输出provider正文、token、会话能力或视频。后续GPU性能复测须等剩余
免费额度可覆盖90秒预留且至少1次免费调用；本次诊断后没有再次申请GPU。
