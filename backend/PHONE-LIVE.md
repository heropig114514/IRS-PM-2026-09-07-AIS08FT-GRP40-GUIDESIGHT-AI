# 2026-10-07 HF 实测、30FPS 发布与启动传输修复

当前公开页面已协调部署30FPS输入/12帧在途窗口和首帧传输预热。Chrome→公网PRS→
真实HF ZeroGPU→Chrome的601帧独立短测完整交付，**稳态29.5357FPS**，600个非首帧
覆盖20.3144秒；采集到解码P95为317.335ms、最大442.6ms，原≥10FPS且所有非首帧
≤500ms验收通过。首帧11047.8ms单列，没有额外排除启动后的帧或任何慢帧。
输入仍是原公开公交图的canvas MediaStream；包含真实640缩放/JPEG80编码、TLS/WS、
结果验证、顺序显示和image.decode，不包含手机传感器、移动网络和屏幕实际绘制。
此结论仅限此次短测，不宣称55秒整会话、长期稳定性、移动画面多样性或手机性能已验证。

本轮三个视频推理调用均保留原模型、FP16编译CUDA方案、640/confidence0.25/JPEG80，
同一Chrome154.0.8037.98、公开输入与原500ms指标。先在一个分配内做六段对照606帧：
24/8、30/8、30/12、30/12、30/8、24/8。30/8连续结果间隔FPS为26.400/26.565，
30/12为30.029/30.113且两段均无窗口等待；证明八帧窗口限制约300ms往返下的30FPS。
短阶段原口径仍包含阶段重新填充/排空，30/12两段原均值28.461/28.527，不能用另一
间隔口径覆盖原值。606帧全交付，但开头24/8最高704.9ms，整次原延迟验收仍为失败。

随后保持默认30/12，以独立会话601帧验证启动情况，再根据profiling实施修复并同条件复测：

| 独立会话，同为30/12、601/601帧 | 修复前 | 修复后 |
|---|---:|---:|
| 首帧后整体FPS，包含所有稳态停顿 |29.1610|29.5357|
| P95采集到解码 |312.34ms|317.33ms|
| 最大采集到解码 |712.2ms|442.6ms|
| 非首帧超过500ms |12帧（index1–12）|0帧|
| 首帧，含分配/恢复/Graph/预热 |9711.4ms|11047.8ms|
| 原完整交付/≥10FPS/最大500ms验收 |失败|通过|

修复前慢帧集中在启动后的前12帧，等待主要在PRS↔HF区间；HF内部每帧约10ms，GPU
forward约0.69ms。结合2026-10-06保留的同网关TCP/CPU profiling及512KiB预热对照，
新增严格协商的`warmup_bytes=524288`：HF首次检测结果准备好后，在原WS发送随机512KiB
`PRW1`控制，PRS原字节返回，HF确认完全一致后才放行首帧。仅一次，双向总计1MiB；
错误/重复确认、缺少控制、3秒超时明确失败，无隐式降级、重试、新GPU调用或视频丢帧。
控制不占用图像序号/帧计数；原5秒worker空闲、55秒会话和55秒冷等待预算不变。
实测预热1661.77ms全部计入首帧，第一帧元数据保留耗时和字节数。P95没有改善，改善
的是启动最大延迟；不将此短测表述为所有未来网络长尾已消失。

修复后平均分段：采集编码7.39ms，PRS↔HF完整返回238.74ms（其中HF本地周期9.67ms，
模型forward0.690ms），浏览器decode3.05ms，采集到解码总计302.77ms。PRS/HF/GPU
区间嵌套，不能相加；worker_pull包含主动等待下一采样，不能当计算耗时。前后预测与
输出JPEG SHA256完全一致，稳态无重复Graph初始化；此单图指纹对照不是新COCO AP评估。

最终HF提交`3d2182c41aaf3f9cde5d20c380c5214c7ba1be8b`，PRS release为
`live-tuning-dd6bfeb8deedfbe9`。新`live-tuning`部署配置仅覆盖摄像头调度/传输及测试，
模型元数据逐字节校验，GPU计算、语音/振动/地图、MCP配置、解释器、systemd/nginx/env
及CPU20%/256MiB/32任务限制保持。原HF提交、两份PRS前序release、全部失败/对照报告
保留；没有本地Git提交，不声称验证单commit原子性。

证据目录为`benchmarks/2026-10-07/fps-live/`：`final-summary.json`为汇总；
`paired-browser.json`、`independent-browser.json`、`warmup-browser.json`保留全部逐帧数据；
部署清单、源码哈希、额度和`provider-release-evidence.log`确认三次worker分别处理并完整
交付606/601/601帧，最终HF健康`active_session=false`。账户额度共消耗94.631748秒、
3次视频调用，剩余205.368252秒/5次。两次源码发布也触发既有启动缓存准备，但首个
视频调用前账户仍为300秒/8次；不将源码发布次数混作视频调用次数。此后未继续推理测速。

检查：117项后端、61项前端测试通过；Django check、迁移dry-run、改动Python Ruff、
改动范围diff检查通过；远端Django/迁移/pip check/ASGI导入/健康/11文件哈希及服务代理
配置哈希核对通过。全仓Ruff仍有历史Kaggle-v11 Notebook I001/E501；全仓diff另有本轮
未修改的i18n/maps-page文件末尾空行提示。systemd验证仍有系统snapd原有RestartMode提示。
没有`tools/check_docs.py`，函数/模块注释及声明/变量索引已人工复核，不宣称自动文档检查。

复测会消耗额度，须先通过90秒免费额度及至少一次调用的门禁。命令：

```powershell
.\.venv\Scripts\python.exe -m tools.check_zerogpu_quota --gpu-budget-seconds 90 --output <新额度报告.json>
$env:PRS_PLAYWRIGHT_PATH='<本机Playwright包绝对路径>'
node ../frontend/tools/benchmark_phone_browser.mjs https://47.239.50.129/prs/live/ 601 <新逐帧报告.json>
```

## 提交快照验证（2026-10-07）

用户要求提交后，独立导出Git暂存区进行检查，80项后端、61项前端测试、Django check、
迁移dry-run、快照全范围Ruff及两个浏览器测速脚本语法检查通过。工作区另有37项历史
未跟踪实验测试留在本地，未删除或禁用任何已跟踪测试；与上方完整工作区117项结果的
范围不同。记录见`fps-live/commit-validation.json`。同时修正CI对已删除检测脚本的引用，
纳入实际使用的两个浏览器测速工具、公开样例、免费额度门禁和本次完整逐帧数据。
上文“未提交”描述的是测速完成时状态；本次将实现、注释/索引、测试、说明一并提交。
HF/PRS在线代码未因此重新部署，也未消耗新的GPU调用。远程CI结果需在推送后单独确认。

# 2026-10-06 30FPS候选调整与零GPU浏览器验证

本地候选已改为30FPS输入、十二个总作业：FramePipeline默认采样与客户端/PRS/HF parent
共享窗口同步调整，顺序校验、显示持有信用、首帧独立完成和错误停止语义保持。模型、权重、
640、batch1、阈值0.25、JPEG80、FP16编译Graph与FP32输出不变；本轮未启用丢帧或预热。
原24/8源码快照及以前真实HF逐帧记录保留。HF19文件候选已在本地内容寻址目录暂存，
没有发布或触发HF构建。公开生产仍24/8、提交2f5dd14e730afe7c94d4bb3a98766e5f4bd6cfb4。

额度API明确拒绝新GPU测试：106.879660秒仍在，但免费次数为0，原因
free_run_limit_exhausted。两次额度读数一致，本轮0次GPU调用、0秒调用者额度扣除。
不能据此宣布真实HF检测已达到30FPS，也没有用付费资源或其他身份绕过次数限制。

真实桌面Chrome154运行CameraApp采集、JPEG80编码、HUD及顺序解码；仅把传输明确替换
成300ms或380ms定时器。假摄像头仍用同一公开图像的30FPS MediaStream，回复是原始JPEG
而非新模型检测；没有视频WS/HF推理。因此这些是浏览器流水线容量控制，不是公网模型测速。
初始两组各846帧，最终节省额度的入口复核606帧，共2298/2298完整交付，未丢弃慢帧。

| 明确CPU控制 | 结果完成间隔FPS（两次） | 实际采集FPS（两次） | 信用等待 |
|---|---:|---:|---:|
| 最终300ms，30/8 | 25.190／25.192 | 25.131／25.162 | 64／73帧，约552／549ms |
| 最终300ms，30/12 | **29.984／29.949** | **30.001／30.000** | **0／0** |
| 固定380ms压力，30/12 | 29.685／29.663 | 29.589／29.619 | 各20帧，约62／55ms |

最终300ms的30/12两段共302帧完整交付，最大采集到解码341.4/340.4ms，最高在途11。
原短测FPS口径仍为28.369/28.381，包含重新填满流水线及排空，不拿间隔率替换原验收。
固定380ms叠加编解码后全程约395ms，十二帧窗口已接近30FPS所需占用，轻微抖动会等待
信用；该控制有意模拟持续高延迟，不等于此前真实链路只有少量帧出现的P95。
这支持普通约300ms场景的30FPS可行性，也指出十二帧方案在接近400ms时余量较小。
原真实HF启动688.5ms慢帧及500ms门槛失败没有被消除或重新标成通过。

测试工具增加显式--30fps平衡顺序：24/8、30/8、30/12、30/12、30/8、24/8；最终
帧数61/91/151/151/91/61，共606。30/12两段保留各150个非首帧，缩短对照段减少后续
HF时长；旧846帧记录及精确运行源码快照均保留，条件变化不隐藏。每一段独立首帧及
完整排空，全连接线序号仍连续，原短测及新增完成间隔率/采集率/信用等待分别记录。

入口在媒体/provider启动前检查所加载客户端的窗口。对公开八帧页面的--30fps实测
得到明确preflight拒绝：phases为空、GPU_calls=0，不会先消耗一次分配后才在十二帧
阶段失败，也不会自动缩小测试窗口。候选十二帧源码的CPU入口已验证正常通过。
最终入口仅澄清scope文字，数值/调度/协议不变；每份报告和运行源码SHA分别保留。

后续真实HF测试只需一次分配，必须先确认90秒预留及至少一次免费调用，并协调发布
HF parent、PRS relay和客户端的十二帧契约。不得只更新一端；本轮暂存而未发布。
可在frontend运行`node tools/benchmark_phone_windows.mjs https://47.239.50.129/prs/live/
OUTPUT --30fps`，当前公开八帧版本会先明确拒绝。零GPU复现使用
`--echo-delay 300 --30fps`或`--echo-delay 380 --30fps`，会加载并记录本地候选三模块SHA。
不自动重试、切换账户或调度未来GPU任务。实际手机帧率、无线网络及长时整链仍待验证。

证据：`benchmarks/2026-10-06/30fps/adjustment/adjustment-summary.json`，同目录包含全量
逐帧CPU报告、公开preflight拒绝、源码前后快照、HF候选清单、生产指纹和额度读数。
检查：108项后端、49项前端测试通过；Django、迁移dry-run、变更Python Ruff及JS语法
通过。完整Ruff仅有旧Kaggle-v11 Notebook的I001/E501；仓库无tools/check_docs.py，
函数说明及声明/变量索引已人工检查。未做Git提交，未声称验证了单次提交原子性。

# 2026-10-06 30FPS目标的阶段诊断与第一跳实测

当前公开运行源码复核仍为24FPS采样、八个总在途作业，SHA与上轮发布值一致。
本次没有更改生产采样/窗口或部署，也没有调用HF GPU。诊断工具增加显式`--rate`，默认
24及原93.5KB/八帧/两个独立连接条件保留；新测试明确使用30FPS，不替换已有24FPS结果。

在原公开HTTPS页面、原`/prs/ws/echo/`接口，用实际桌面Chrome154进行两个尺寸匹配
二进制控制。各121个缓冲逐字节、按序验证，242/242完整返回；不经过HF、模型、JPEG
采集/解码、物理手机无线网络或屏幕绘制。每包双向93500字节，压测上行还略大于原
84682字节图像请求，TLS/CSP均保留。

| 首包后明确空闲条件 | 实际发送FPS | 完成间隔FPS | 非首包延迟中位 | 非首包最大延迟 |
|---|---:|---:|---:|---:|
| 无空闲 | 29.9236 | 30.1992 | 48.30ms | 95.30ms |
| 10秒空闲 | 29.9899 | 30.2391 | 47.35ms | 95.40ms |

完成间隔略超过30是端点延迟下降带来的短窗口差异，结论为约30FPS，不解释成超过供给
的吞吐。首包213.8/200.7ms单列。尺寸控制实际双向各约22.38/22.43Mbps，说明当前桌面
浏览器↔PRS的第一跳可以承载约30FPS流量；不能据此声明真实手机或整条HF检测已达30。

现有真实HF会话逐帧复算：PRS↔HF完整区间中位238.251ms，其中HF本地取帧到结果
校验周期13.192ms；浏览器JPEG采集6.90ms、解码3.05ms，全程中位296.70ms。
逐帧用transportMs扣除对应PRS upstream/admission时长，第一跳/PRS重新组包与发送/
浏览器校验余量中位46.935ms，与独立Echo结果量级一致；这是持续时间之差，不相减
跨主机绝对时间戳，也不是纯网络RTT。嵌套中位区间不能全部直接相加。

30FPS每次供给间隔33.33ms，已测GPU本地周期及浏览器编解码服务时间均有余量。
当前直接限制先是24FPS输入上限，再是完整流水线仅八个信用：按约300ms占用估算需要
约9–10个在途名额；前次P95约381.265ms对应约12个。12是后续候选窗口，不是已发布
参数，也不能保证启动688.5ms慢帧已经解决。更长HF往返等待与有限窗口的组合，会触发
应用层回压；不能再次把稳定RTT本身称为必然FPS瓶颈。

原JPEG体量下30FPS需上传约20.324Mbps、回传约22.440Mbps。上轮私有CPU同网关大窗口
控制完整回传102.51–106.44FPS，对可用容量有支持，但不是正式Space的30FPS检测测速。
目前证据主要支持配置/在途容量限制，尚未证明原始带宽上限是限制因素。

后续真实30FPS验证应同步调整采样及PRS/HF/客户端窗口协议，明确对照24/8与30/12；
模型、640、阈值、JPEG80与输出保持。同样保留启动慢帧、全程时间和丢帧计数。
传输预热/丢帧策略需分别记录为独立条件，不会为了30FPS目标静默改变原完整交付测试。

完整结果：`benchmarks/2026-10-06/30fps/diagnosis-summary.json`及同目录第一跳原始逐帧JSON。
工具JS语法、48项前端和108项后端测试、Django check及迁移dry-run通过；完整Ruff仍
只有原Kaggle-v11 Notebook的I001/E501。仓库无tools/check_docs.py，变更函数说明及声明/
变量索引已人工核对。真实HF整链30FPS、长时稳定性及物理手机尚未验证；未做Git提交。

# 2026-10-06 吞吐归因修正：稳定RTT与流水线回压

稳定约200ms的RTT本身不应降低24FPS吞吐：只要各阶段服务能力、链路吞吐和在途窗口
足够，传播延迟主要增加单帧响应时间。此前将“传输占单帧延迟最大”笼统称为“FPS最大
瓶颈”，混淆了两个指标。此次直接复算已保存的真实HF逐帧记录，0次GPU调用，不改运行
源码、模型、窗口、实验参数或验收门槛。

实现已采用异步流水线：FramePipeline除第一帧独立完成及窗口满时，不等待每帧结果再
采集；PRS接收、上行发送和下行返回分别运行。浏览器信用直到校验、按序解码显示完成
才释放，所以窗口需按**完整采集到显示的占用时间**配置，不能只按TCP RTT配置。
若24FPS且全程占用约302ms，平均需要约7.25个在途作业，五个信用会回压，八个基本足够。
稳定200ms且无其他开销时则平均约4.8个；各阶段串行服务时间和带宽也需满足供给速率。

同一次HF分配、同图像/模型/JPEG80条件的复算，仅排除该阶段独立的第一帧：

| 24FPS条件 | 采集间隔FPS | 结果完成间隔FPS | 单帧延迟中位 | 信用等待 |
|---|---:|---:|---:|---:|
| 五帧窗口 | 16.5197 | 16.5117 | 301.95ms | 78帧，共2214.1ms |
| 八帧窗口 | 23.9794 | 23.9678 | 302.45ms | 1帧，3.5ms |

五帧组119个采集间隔理想4958.33ms，实际7203.50ms，多2245.17ms；其中2214.1ms
来自显式creditWaitMs，约占98.6%。八帧组实际4962.60ms，接近理想节奏。
这是**有限并发导致的应用回压**，并没有因增加窗口而降低单帧传播延迟。

原名steady_fps的短测口径为：(总帧数−1)/(末帧完成−独立首帧完成)。虽然单列首帧冷
耗时，但它仍包含首帧后重新填充流水线的延迟及启动停顿，不能直接当作持续稳态输出率。
保持原始指标及验收不变，另用相同非首帧集合的(K−1)/(末帧完成−第一张非首帧完成)
记录完成间隔率；若端点延迟在变化，该指标也会受输出集中返回影响，应结合采集间隔、
全程慢帧和稳定时段解释，不能挑选更高值替代验收。

正式新会话181帧的原22.1880FPS分母为8112.5ms，可分解为首帧完成后到下一次采集
0.7ms、非首帧采集跨度7817.5ms、末帧采集到解码294.3ms。179个采集间隔理想
7458.33ms，实际多359.17ms；显式信用等待合计354.8ms。等待位于第10/12/16/148帧，
其中前三次合计353.3ms，后一次仅1.5ms，主要是启动瞬态后窗口满造成停采。
原先已报告的帧12–181诊断范围采集23.8697FPS、完成23.8714FPS，保留其范围和限制。
因此当前持续吞吐已接近24FPS；仍需处理的是启动网络排队/窗口爬升造成688.5ms慢帧，
以及随后的八帧信用耗尽。原22.1880FPS报告、2–9帧超过500ms及失败验收保持不变。

复算证据和公式：`benchmarks/2026-10-06/fine-profiling/pipeline-throughput-attribution.json`。
两份来源JSON的SHA已记录；重新算出的采集率与原报告吻合，分母分解闭合。此次仅更新
诊断文档及派生数据，没有运行新外部推理/性能测试，也未做Git提交。

# 2026-10-06 启动传输定位续测：双向窗口与预热路径

本轮**0次GPU调用、0秒调用者GPU额度扣除**。只在原私有探针Space临时运行CPU诊断服务，
正式HF提交`2f5dd14e730afe7c94d4bb3a98766e5f4bd6cfb4`保持，未部署未经真实GPU验证的
预热策略。探针已恢复原forward的README/app.py/requirements.txt逐字节内容并PAUSED，
临时回环进程和SSH隧道已停止；现仍剩106.879660秒、0次免费调用。

输入通过同一Chrome/CameraApp/公开图流导出：84682字节、480×640，SHA与以前相同。
使用已记录的真实检测框及现有JPEG80渲染函数生成的91181字节JPEG，SHA也与真实GPU返回
完全相同。CPU诊断固定整包93500字节，显式13.2ms处理等待、10秒首帧初始化等待；
不运行模型、CUDA或任何GPU API。服务返回`probe_result/gpu=false`，不能当成新检测结果。
协议压缩关闭，JSON填充仅保证整包大小；所有图像序号、慢帧和控制成本都保留。

固定同一当前DNS网关`3.211.147.15`，原URI主机仍用于TLS/SNI/Host校验，不关闭TLS验证。
前后反向顺序各一次，共10个新连接控制；每个24FPS组121帧，压力组240帧，全部完整交付。
以下是**PRS主机↔私有HF CPU控制**，排除手机、浏览器编解码及真正GPU处理。

| 明确控制条件 | 两次首帧后FPS | 两次最大稳态传输延迟 | 首帧约 |
|---|---:|---:|---:|
| 原24FPS／八帧窗口 | 22.18／22.13 | 470.3／475.1ms | 11.04s |
| 初始2帧、逐结果增加至8 | 21.15／21.75 | 444.3／402.0ms | 11.05s |
| 首个结果准备后256KiB双向控制预热 | 22.58／22.66 | 435.5／418.9ms | 12.22s |
| 首个结果准备后512KiB双向控制预热 | **23.08／23.05** | **275.2／287.6ms** | 12.56s |

512KiB组实际采样23.991/23.995FPS。第一次正常图仍独立完成，预热是单独的PRW1
控制回显，不计为图像/推理；其约1.5秒及双向约1MiB流量都进入首帧冷成本。
这不是删除前几张慢图，其他120张图继续使用原FPS和延迟定义。
渐进窗口减少早期排队，但短测吞吐下降；256KiB效果有限。512KiB双向预热是目前最有
证据的候选，**CPU控制通过不意味着正式GPU的500ms门槛已经通过**。

独立容量组明确使用120FPS供给、32个诊断信用、0ms处理/初始化及1MiB双向预热，
不与正常24FPS/八帧条件混淆。实际完整回传102.51–106.44FPS，输入有效负载约
69.45–72.11Mbps、返回约76.68–79.62Mbps。当前视频需要约16.3/18.0Mbps。
这证明该网关路径的可用吞吐明显高于目标视频需求，**不是最大物理带宽测速，也不能
完全排除生产Space特有的调度/限速**；不能把窗口上限称为带宽上限。

10ms Linux TCP_INFO进一步显示，普通组初始拥塞窗口69个MSS，未发送字节最高约350KB。
512KiB组进入视频时拥塞窗口约432个MSS，未发送峰值约56KB，服务队列最大约0.05ms。
这些样本没有重传，接收端限制累计时长也未增长；仍保留原始重传/窗口计数。
在具备空TLS用户缓冲、零重传条件的写入检查点，估算请求TCP字节边界，再结合采样ACK：
部分启动图上传确认等待约403ms，另一些图上传确认后还等待约235ms；后者服务处理
只有约14ms。等待涉及**上传和返回两个方向**，不是只有PRS发送队列。ACK检查点为采样
估计而非包级抓包；服务器/客户端绝对时钟不相减，只比较各自持续时间和间隔变化。

原正式HTTPS浏览器↔PRS Echo另做93500字节、24FPS/八个在途的原样二进制控制。
各121个缓冲完整返回，无空闲/首包后10秒空闲时稳态最大88.7/85.4ms，首包212.5/219.3ms。
该控制不包含捕获/解码，亦不包含HF，却未复现持续200ms级额外等待。

自动审批在执行前拦截了新增临时Nginx路由/systemd服务的命令，仅返回`blocked by policy`，
没有更具体原因；该命令没有执行。替代实验用SSH转发的回环页面，保留同源CSP及HF TLS
校验，不改Nginx/systemd。真实Chrome/CameraApp/FramePipeline四组全部121/121交付，
但SSH第一跳带来显著额外等待，中位解码约447–464ms、输出15–16FPS，故不能拿它解释
原HTTPS的22FPS或验证正式500ms门槛。早期诊断客户端在摘要到达后过早开下一单槽连接，
产生1013；已通过等待服务器正常关闭修正，失败记录与最终结果均保留，没有自动重试。

建议下一步的具体实现边界：HF首个真实结果已经准备好时，先请求一次最多512KiB的
双向控制预热；PRS验证回显完整性后再允许首次JPEG发送。图像仍用原PRF1/PRS1序号，
不丢帧、不改变JPEG/阈值/模型；Root身份与worker私钥继续仅留服务端。
必须显式协商新控制协议、限为每会话一次，并给出**短于worker5秒空闲预算**的预热
失败期限；失败明确结束，不回退。预热/首帧冷时间、额外流量及普通帧指标各自记录。
这条路径的CPU原型在`tools/cpu_transport_probe.py`里已运行，但生产实现与真实GPU
验证尚待免费次数恢复；本轮没有用未经验证的方案更改正式推理接口。

完整证据在`benchmarks/2026-10-06/startup-transport/continuation-summary.json`：固定IP
矩阵、逐帧ACK/服务时间、10ms字节轨迹、原HTTPS第一跳、SSH限制/失败、原文件恢复和
额度读数全部在同目录。工具为`profile_transport_startup.py`、`cpu_transport_probe.py`、
`cpu_transport_proxy.py`及前端三个独立诊断入口；其中临时私有运行源码提交为
`61e8d06419d04e6aeea24a5e200f74ce0eee7d0e`，本地后续仅增加更明确的路由拒绝及日志。

检查：当前108项后端、48项前端测试通过；新增5项实际本地socket/协议测试，变更Python
Ruff、JS语法、Django及迁移dry-run通过。完整Ruff仍只有旧Kaggle v11 Notebook的I001/E501；
仓库没有tools/check_docs.py，注释和声明/变量索引人工复核。未做Git提交。

以下是上一轮真实GPU定位与已发布的24FPS/八帧配置记录。

# 2026-10-06 精细链路 profiling 与八帧窗口修复

当前已发布**24FPS输入上限、八个总作业**。真实Chrome新会话181/181帧全部交付，
首帧10.996秒单列，原口径首帧后输出**22.1880FPS**、实际采集22.8973FPS；
P95采集到解码381.265ms、最大688.5ms。吞吐提高，但原500ms最大延迟门槛仍失败，
`meets_target=false`保持。图源仍为公开公交车图片的MediaStream，不包含物理手机摄像头、
移动CPU/无线网络或最终屏幕绘制。原12FPS/五帧条件及全部旧结果保留。

先做零GPU的协议Ping/Pong、JPEG上传确认、Linux TCP_INFO和真实Chrome合成传输控制，
再用一次HF分配做四个明确阶段，共364帧。模型/权重/640/batch1/FP16编译方案/阈值0.25/
JPEG80/FP32输出不变；各阶段使用独立首帧与完整排空，客户端线序号在同一连接内连续。
每段首帧单列用于FPS计算，整个会话的500ms门槛仍只排除真正的第一帧，其他慢帧全保留。

| 同一次实际HF分配 | 首帧后输出FPS | 实际采集FPS | 稳态最大解码延迟 |
|---|---:|---:|---:|
| 12FPS／五帧，61帧 | 11.1661 | 11.6254 | 553.7ms |
| 24FPS／五帧，121帧 | 15.9834 | 16.5197 | 329.6ms |
| 24FPS／八帧，121帧 | **22.7661** | **23.9794** | 336.0ms |
| 12FPS／八帧，61帧 | 11.4679 | 11.9666 | 322.3ms |

全部364帧完整交付，Graph未重复捕获，raw/JPEG哈希全部一致。五帧到八帧主要消除
高RTT下的信用等待：24FPS阶段等待帧从78降至1，HF输入队列没有随窗口扩大而积压。
不同输入上限、窗口和时段的结果不能当作模型forward加速比；表中是单分配描述性对照。
整个分配仍因12FPS/五帧阶段553.7ms而未通过最大延迟验收。

精细分区来自同一进程内的单调计时，不相减跨主机时间戳。正式24FPS/八帧新会话中位数：

| 可定位区间 | 耗时 | 解释 |
|---|---:|---|
| PRS接收后等待转发 | 0.050ms | 本地应用排队很小 |
| HF完整输入到worker取走 | 0.135ms | GPU前父进程队列很小 |
| HF父进程取帧响应准备到完整结果校验 | 13.192ms | 本地交接、推理及结果上传的完整周期 |
| worker完整predict | 11.352ms | 包含预处理、GPU、后处理和元数据构造 |
| 上两项中位数差，仅作近似交接余量 | 1.840ms | 不是约200ms瓶颈 |
| HF结果出队等待 | 0.264ms | 下游应用队列很小 |
| PRS完整结果校验 | 0.384ms | 校验开销很小 |
| PRS↔HF完整区间 | **238.251ms** | 主要等待位于上述内部周期之外 |
| CUDA event forward | 0.7156ms | 包含在predict中，不能再与其相加 |

按每帧扣除互不重叠的HF输入队列、本地周期、输出队列和PRS校验后，剩余区间中位数
224.225ms；它仍包含网络、边缘代理、TLS、序列化及ASGI发送，不能称作纯网络RTT。
worker_pull_ms包含等候下一张按节奏采集的图，不将它误判成HTTP固有开销。
关键状态与每60/120帧记录有限日志；metadata保留完整分区，不保存摄像头媒体。

协议Ping/Pong对照本机→PRS约39.28ms、本机→HF约243.34ms、PRS→HF约229.28ms；
PRS内核TCP RTT约210–223ms，TCP_NODELAY已启用。Windows库内置latency使用的事件循环
时钟有可见量化，因此最终Ping测量改用显式perf_counter，原始两套读数均保留。
真实JPEG上传后再等Pong仅确认上传，不含图像结果下载，不替代完整推理延迟。

最后一次真实会话还读取PRS进程cgroup拥有的TLS socket状态：媒体连接在视频开始时
`cwnd=70`、MSS1448，即约101KB在途容量，已有**71804字节notsent**；下一秒采样
`cwnd=718`，媒体字节流恢复到稳定速率，接收自调节指标rcv_space也明显增长。
TCP RTT保持约212ms。后续SendQ约424KB主要是已发送未确认的在途数据，不能全部当作
未发送积压。此观测支持**启动传输能力爬升时的字节级发送排队**，而不是HF模型/应用
队列积压；一秒采样尚不能逐帧分清所有TLS/代理事件或完整排除网络损失。

181帧正式测试超过500ms的帧是2–9，共8帧；最慢第8帧688.5ms中，PRS↔HF631.818ms、
HF输入队列20.791ms、本地周期11.182ms、服务9.666ms、forward0.7322ms。仅作定位的
帧12–181完成间隔约23.8714FPS、最大334.8ms，**不替代22.1880FPS官方短测，也不改变
失败验收结果**。这表明剩余尾延迟集中在开始连续视频的初始阶段。传输预热或渐进放开
初始窗口值得下一轮验证，本轮没有部署未经HF验证的启动策略；55秒持续表现也未测。

检索并核对了[websockets延迟/保活说明](https://websockets.readthedocs.io/en/15.0/topics/keepalive.html)、
[缓冲与背压说明](https://websockets.readthedocs.io/en/stable/topics/memory.html)、
[FastRTC物体检测实现](https://github.com/gradio-app/fastrtc/blob/main/demo/object_detection/app.py)及
[SANA-WM的ZeroGPU流式实现](https://huggingface.co/spaces/multimodalart/sana-wm-streaming/blob/main/app.py)。
采用长连接、独立收发和有界队列的思路；SANA-WM的丢帧策略会改变本项目完整交付契约，
因此本轮使用有界信用窗口。没有更换图像编码、模型或引入WebRTC来混淆比较。

100ms协议Ping保活进行了明确实验。10秒空闲的CPU对照里，下一张74KB上传确认从
417.8/435.7ms缩短为224.4/230.0ms；但实际24FPS/八帧181帧复测仅22.1607FPS、最大
688.1ms，未解决启动尖峰。保活并未增加GPU申请，Ping心跳超时设为None，原响应预算
保持；然而真实收益未确认，**已明确撤回**该实验，代码、失败结果与撤回记录保留。
还查明PRS的tcp_slow_start_after_idle=0，媒体socket在GPU初始化期间cwnd始终70；
因此不能把问题归因于“该选项引起空闲窗口回退”。实验没有修改任何系统TCP配置。

当前PRS为`phone-24fps-5596b747c05363ce`，原12FPS版本、八帧过渡版本及已撤回的保活
版本均保留。公开三个JS及三个后端模块与本地源码逐字节一致，HF新提交为
`2f5dd14e730afe7c94d4bb3a98766e5f4bd6cfb4`，只改变运输边界/计时及文档，质量计划保持。
HF health ready、active_session=false；每次实际会话的PRS日志独立核对完整交付计数。
源码发布沿用原startup GPU初始化，重新部署前后的调用者免费额度读数未减少；该startup
工作没有在本次调用者计费差额内计时，不声称整个部署过程没有GPU工作。

本轮三次实际GPU调用，分别扣36.207295、18.527998、17.982993秒，共**72.718286秒**。
剩余106.879660秒、**0次**免费调用，门禁现因次数耗尽拒绝新申请。官方runs reset为
2026-10-06T16:08:22.097Z，即北京时间2026-10-07 00:08:22；没有再申请或付费扩容。

数据在`benchmarks/2026-10-06/fine-profiling/`，总汇为`fine-diagnosis-summary.json`，
包括全部帧、各次源SHA/发布/撤回、TCP/Ping/空闲控制、完整失败与额度。新工具
`frontend/tools/benchmark_phone_windows.mjs URL OUTPUT [--echo-delay 300]`明确区分零GPU
合成控制和单次真实分配；`benchmark_phone_browser.mjs URL 181 OUTPUT`使用公开正式页面。
复现真实模式必须等待免费次数恢复并先做90秒预留检查，不自动重试。

交付检查：101项当前后端测试、48项前端测试通过；Django check、迁移dry-run、变更Python
Ruff、JS语法及远端依赖/ASGI/就绪检查通过。完整Ruff仍有历史Kaggle v11 Notebook的
I001/E501两项问题，全工作区diff仍报告未修改的maps-page.js末尾空行。仓库没有
tools/check_docs.py，注释及声明/变量索引人工复核；未做Git提交，未声称提交原子性。

以下为此前12FPS/五帧调度修复及初始测试记录。

# 2026-10-06 FPS profiling、历史24FPS口径核对与调度修复

已修复浏览器采样的累计定时器漂移并发布。相同61帧短测中，完整浏览器输出由
**10.6323提升至11.1204FPS**，实际采集由11.0206提升至11.6323FPS；前后均61/61完整交付。
关闭CPU采样profiler的零GPU控制中，12FPS采集由11.1899恢复到12.0007；显式250ms
合成Echo往返时由11.3612恢复到11.9887。当前公网仍未达到24FPS，最大解码延迟567ms，
原有500ms验收门槛未通过，`meets_target=false`保留。未删除慢帧或改变验收条件。

此前约24FPS的三个记录分别是`2026-10-03/resident-continuous.json`的23.9966、
`resident-independent-service-smoke.json`的23.9195和`zerogpu-live-native-bridge.json`的
24.0240FPS；均为本地RTX4060、loopback WS或loopback HTTP桥，排除公网、物理摄像头及
浏览器采集/解码。第三份明确`local_native=true`。此前同一公网HF接口预编码输入为
12FPS时输出12.0012FPS。本轮一次GPU分配依次测12/24FPS，4张正确性输入＋48＋96帧
全部通过身份/顺序/预测哈希检查，输出分别为**12.0126/17.2679FPS**。因此没有观察到
同路径12FPS接口吞吐退化；历史本地24FPS不能直接视为当前公网24FPS基线。

| 当前预编码公网接口 | 12FPS输入 | 24FPS输入 |
|---|---:|---:|
| 实际输入FPS | 12.0016 | 17.2780 |
| 完成结果FPS | 12.0126 | 17.2679 |
| 请求到已校验结果中位数 | 289.403ms | 288.624ms |
| PRS↔HF区间中位数 | 236.125ms | 235.145ms |
| 服务处理／CUDA event forward中位数 | 12.879／0.7148ms | 12.852／0.7157ms |

五帧在途窗口在约289ms平均占用下的容量估算约17.3FPS，与24FPS输入实验吻合。
实际采样在取得空位后进行，最大采样迟延1540ms如实保留，未积压旧图或丢帧。
本轮零GPU网络控制还发现，PRS主机到HF的CPU-only健康请求，在复用HTTPS连接时
8个稳态请求中位数242.356ms；本机到PRS页面为45.221ms。健康接口不分配GPU，
这些小HTTP响应仍含代理/服务处理，与二进制WS的路由、大小和队列不同；它们支持较大
等待存在于跨服务链路，不能将全部耗时标成纯网络RTT或进一步确定代理/进程交接比例。

浏览器原调度每帧使用`实际采集时间＋间隔`，普通定时器晚醒会累积。修复前60个稳态
帧的晚醒平均5.773ms，capture平均8.745ms、decode平均2.420ms；信用等待仅2帧。
修复保持预定时间线，普通晚醒从下一次剩余等待中扣除；等待信用或迟延达到整帧间隔时
重新从实际采集时刻安排，不重放错过的采样槽。首帧仍独立完成，默认12FPS、最多五个
包含采集/传输/等待显示的作业、模型权重/FP16方案/640/阈值0.25/JPEG80保持。
每帧增加预定时刻、请求/实际等待、晚醒及信用等待观测；页面仅每5秒和停止时汇总，
不保存无限帧历史、媒体或凭据。缺少调度字段的串行Echo不会被当作零等待样本。

| 相同Chrome、61帧公网完整路径 | 修复前 | 发布后 |
|---|---:|---:|
| 首帧后结果解码FPS | 10.6323 | 11.1204 |
| 实际采集FPS | 11.0206 | 11.6323 |
| 首帧单列 | 13.918s | 11.453s |
| 解码延迟中位数／P95／最大 | 294.85／454.25／540.5ms | 316.00／465.165／567.0ms |
| PRS↔HF／服务处理／forward中位数 | 237.070／13.458／0.7252ms | 257.723／11.613／0.6978ms |

输出提升4.59%是两次短分配的描述性观察，不是独立分配重复得到的确定收益；后一次网络
区间及采集耗时反而更长。零GPU控制与调度回归测试独立支持漂移修复。发布后有一次
148.1ms信用等待，说明网络抖动仍会阻塞采样；约5.07秒采集跨度、首个稳态采集偏移和
末帧返回/解码均进入短测5.40秒完成区间，不能将实际采集FPS替代完整输出FPS。
最慢567ms帧中transport555.2ms、PRS↔HF476.104ms、服务12.464ms、forward0.7016ms、
decode2.2ms；没有重复Graph捕获，稳态预测/JPEG哈希均稳定。20% PRS CPU额度不变，
12/24接口实验没有新增throttle；重启后的浏览器测试仅新增一次36.098ms throttle，
与启动期累计1.844秒分开记录，不能解释每帧约250ms的跨服务等待。

CPU采样profiler自身可能改变定时器行为：开启1kHz采样的12FPS零传输控制采集11.893，
关闭后修复前11.190。原profile与帧计时保留；实际HF修复前后与主要控制比较均关闭
CPU采样，另用CDP Performance累计时长观察。控制执行真实Chrome/CameraApp/canvas
编码/解码/HUD，传输为明确的0或250ms合成Echo，不代表网络或模型验收。

仅发布`camera.js`、`frame-pipeline.js`、相应两个测试及前端README五个文件；对整个
新release的普通文件哈希比较确认其余134个文件继承原release。当前为
`phone-cadence-ceb81d60166dcab1`，原`phone-live-4838d71f86078c02`保留。公开三个运行
JS的SHA与本地一致，HF仍为`eb063ebcccc4461a77c38341456e3dbf22aed962`，没有重启HF或
重编译模型。PRS检查及服务就绪通过，HF健康ready且active_session=false。

本轮诊断共三次GPU调用，扣除59.074721秒：同分配12/24接口23.767360秒、修复前浏览器
19.005585秒、发布后16.301776秒。剩余179.597946秒、3次免费调用；CPU控制、profiling
与健康测量均不调用GPU。物理手机传感器/CPU/无线网络/最终绘制及55秒持续性能未验证。
若后续要求公网24FPS，同样五作业窗口需要平均总占用不超过约208ms；需缩短跨服务路径
或显式重设计窗口/采样并验证延迟，单独加速当前约0.7ms forward不足以消除该瓶颈。

复现入口：前端`tools/profile_frame_pipeline.mjs URL OUTPUT PIPELINE_FILE [--cpu-profile]`
及`tools/benchmark_phone_browser.mjs URL 61 OUTPUT [--pipeline-file PATH]`；实际发布后
复测未使用源码覆盖。后端`tools.benchmark_live_remote`参数为同一URL/origin、
`--fps 12 24 --seconds 4 --plan models/inference-plan.json`。全部原始行、源文件前快照、
源SHA、CPU profile、额度、发布、日志和分区计算在`benchmarks/2026-10-06/profiling/`，
汇总为`diagnosis-summary.json`；旧报告保持。

检查：40项前端和100项后端测试通过；Django check、迁移dry-run、变更Python Ruff/语法、
JS语法及远端Django/迁移/依赖/ASGI检查通过。完整Ruff仍仅有旧Kaggle v11 Notebook的
I001/E501两项问题；全工作区diff检查仍有本次未修改的maps-page.js末尾空行。
仓库没有`tools/check_docs.py`，变更文件注释及声明/变量索引人工复核；未做Git提交。

以下为本日修复前的FPS与forward对比记录。

# 2026-10-06 当前摄像头 FPS 与 HF forward 对比（修复前）

当前公网页面短测 **10.3118 FPS**：61/61帧全部交付，首帧12.190秒单列；其后60帧
在5.8186秒内完成解码。默认输入上限仍为12FPS、最多五个作业，未改变采样策略、模型、
640输入、阈值0.25或JPEG80，也未删除慢帧。真实Chrome执行页面/canvas编码/公网WS/
SHA校验/顺序图像解码；公开公交车图片生成的MediaStream仅替代物理摄像头。
因此结果不包含手机传感器、手机CPU/无线网络或最终屏幕绘制，也不是55秒持续运行结论。

采集到解码中位数302.7ms、P95为412.555ms、最大522ms；首帧后的Graph没有重复捕获，
raw/JPEG哈希全部稳定。完整交付与≥10FPS通过，最大延迟仍超过既有500ms门槛，
`meets_target=false`如实保留。PRS日志独立确认received/forwarded/delivered/worker均为61。

浏览器路径稳态中位数：CUDA event forward0.732224ms，含传输GPU推理1.901715ms，
完整服务处理11.951792ms，PRS↔HF区间246.485082ms。最慢帧522ms中，PRS↔HF为
461.380890ms。较大等待在forward之外；这些区间不能单独区分网络、排队与进程交接。
公开camera.js、frame-pipeline.js和live-detection-client.js的SHA均与本地源码一致。

有余量后使用隔离、私有的`Heropig114514/prs-forward-probe-20261006`，在**一次GPU分配**
内比较三个方法；正式摄像头Space没有重启或改源码。实际硬件与正式服务一致：
NVIDIA RTX PRO6000 Blackwell Server Edition MIG2g.48gb，94SM，Torch2.8.0/cu128。
五个公开输入为公交车图及原四张COCO8图，沿用JPEG80/项目letterbox、batch1/640、
seed20261004；TF32和cuDNN benchmark关闭，CPU/intra-op与inter-op均一线程。
每个方法至少10次且1秒预热；三轮轮换方法顺序，每轮每方法30个样本，共270个，全部保留。
计时使用同一CUDA stream上的start/forward/end事件并同步end；输入已驻留GPU，排除
H2D/D2H、JPEG、网络、渲染、加载、预热及编译，包含一致的FP32输出转换。
普通eager内核之间的CPU提交空隙保留在CUDA event elapsed time内，不声称是内核执行时间之和。

| 方法 | forward中位数 | 当前方案相对该方法的加速比 |
|---|---:|---:|
| 原实验FP32 eager，NCHW，未融合Conv/BN，114个BatchNorm | 8.979840ms | **12.9086倍** |
| 相同FP16、Conv/BN融合、channels-last的eager | 6.631296ms | **9.5325倍** |
| 当前FP16、融合、channels-last、compile(default/fullgraph/static)、生产CUDA Graph | 0.695648ms | 1倍 |

12.9086倍包含精度、融合、布局及编译/Graph的整体收益；9.5325倍控制了前三项，
表示编译加Graph的联合收益，不能进一步拆成单项收益。三轮对FP32 eager为
12.7677/12.8110/12.9254倍，对同FP16 eager为9.6581/9.8144/8.9926倍。
这是一次短分配内的描述性对比，未重复独立GPU分配，也不形成HF全方法排名。
该forward加速比不能替代公网FPS加速比。四张COCO8的当前方案raw SHA均与此前正式
HF缓存推理记录完全一致；eager与compiled的原始top-k行序差异仅作诊断，没有新增AP结论。
原有三次完整5000图COCO资格证据保持，HF没有重新运行完整COCO。

临时探针冷编译33.909秒、Graph准备2.964秒、分配内总工作41.857秒分别保留。
初次CPU启动因Gradio6 API注册参数不匹配而失败，保留错误报告并改为项目现有的隐藏
Button.click注册方式；该失败没有调用GPU，也未调整数值条件或自动重试GPU。
测试完成后临时Space已PAUSED，源码与结果保留。正式HF提交仍为
eb063ebcccc4461a77c38341456e3dbf22aed962，CPU健康ready且active_session=false。

两次免费GPU调用合计扣除**61.327333秒**：页面短测17.391253秒，forward对比
43.936080秒；剩余238.672667秒、6次免费调用。没有申请付费硬件或继续消耗额度。
90秒是每次默认60秒Blackwell任务的准入预留，实际扣除量以上述额度差额为准。

原始证据在`benchmarks/2026-10-06/`：`browser-61frames.json`、`browser-summary.json`、
`browser-relay-log.txt`、`public-camera-source.json`、`forward-comparison.json`、
`forward-summary.json`、`forward-runtime-log.json`、`quota-before.json`、
`quota-after-browser.json`、`quota-after-forward.json`及`production-after-tests.json`。
探针源为`tools/hf_forward_probe.py`，单调用/额度与原始样本校验入口为
`tools/benchmark_hf_forward.py`；原失败、创建、提交与暂停记录一并保存。

检查：Django check、迁移dry-run、现有100项后端测试、新增两脚本Ruff和Python语法检查
均通过；从原始计时重新计算FPS/forward中位数/加速比，并核对61帧交付、输出身份、
额度差额、正式源码提交及暂停状态。完整Ruff仍有历史Kaggle v11 Notebook的I001/E501
两项问题；工作区git diff --check还报告本次未修改的frontend/maps-page.js末尾空行。
仓库没有tools/check_docs.py，两脚本注释和声明/变量索引已人工复核；未做Git提交，
未声称验证提交原子性。物理手机、55秒持续FPS、独立分配重复forward与新的HF完整AP未验证。

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
