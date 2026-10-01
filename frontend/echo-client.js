/**
 * Responsibilities: 单条 WebSocket 的连接、ping/pong、单帧字节验证及明确错误语义。
 * Implementation: 同时仅一个在途操作；超时、断线或内容不一致立即停止，不重连或重试。
 * Related Modules: camera.js, backend/core/echo.py, tests/echo-client.test.mjs。
 * Declaration Index: browserSocket; EchoClient; EchoClient.constructor; EchoClient.connect;
 * EchoClient.wait; EchoClient.wait.registerRequest; EchoClient.ping; EchoClient.sendFrame;
 * EchoClient.handleOpen; EchoClient.handleMessage; EchoClient.handleError; EchoClient.handleClose;
 * EchoClient.handleTimeout; EchoClient.complete; EchoClient.fail; EchoClient.stop。
 * Variable Index: TIMEOUT_MS — 10 秒连接/回传超时；MAX_FRAME_BYTES — 1 MiB 原始帧上限。
 */
export const TIMEOUT_MS = 10000;
export const MAX_FRAME_BYTES = 1024 * 1024;

/** Functionality: 构造浏览器 WebSocket。Inputs: url。Outputs: 新 socket。
 * Logic: 使用原生 WebSocket；Constraints: 只在浏览器运行，测试通过显式注入替换。 */
function browserSocket(url) {
  return new WebSocket(url);
}

/** Functionality: 管理可校验的单请求回传连接。
 * Logic: pending 保存操作种类、原始字节/标识、时间和 Promise 回调；完成或停止时释放。
 * Constraints: 同一实例只连接一次；不能并发发送帧与 ping；无自动重连。 */
export class EchoClient {
  /** Functionality: 初始化会话。Inputs: url、onDisconnect 回调和可注入 socketFactory。
   * Outputs: 实例；Logic: 保存依赖并绑定事件；Constraints: 不立即连接或读取媒体。 */
  constructor(url, onDisconnect, socketFactory = browserSocket) {
    this.url = url;
    this.onDisconnect = onDisconnect;
    this.socketFactory = socketFactory;
    this.socket = null;
    this.pending = null;
    this.sequence = 0;
    this.stopped = false;
    this.notified = false;
    this.handleOpen = this.handleOpen.bind(this);
    this.handleMessage = this.handleMessage.bind(this);
    this.handleError = this.handleError.bind(this);
    this.handleClose = this.handleClose.bind(this);
    this.handleTimeout = this.handleTimeout.bind(this);
  }
  /** Functionality: 开始一次连接。Inputs: 保存的 url/工厂。Outputs: 连接 Promise。
   * Logic: 先登记等待再绑定事件；Constraints: 重复连接拒绝，二进制固定 arraybuffer。 */
  connect() {
    if (this.socket) throw new Error("同一连接不能重复启动");
    this.socket = this.socketFactory(this.url);
    this.socket.binaryType = "arraybuffer";
    const result = this.wait("connect", null);
    this.socket.onopen = this.handleOpen;
    this.socket.onmessage = this.handleMessage;
    this.socket.onerror = this.handleError;
    this.socket.onclose = this.handleClose;
    return result;
  }
  /** Functionality: 登记唯一在途操作。Inputs: kind、expected 原始字节或 ping id。
   * Outputs: Promise；Logic: 注册回调及 10 秒计时；Constraints: 已停止或已有 pending 则拒绝。 */
  wait(kind, expected) {
    if (this.stopped || this.pending)
      throw new Error("连接已停止或已有数据等待返回");
    const client = this;
    /** Functionality: 保存 Promise 回调。Inputs: resolve/reject。Outputs: 无。
     * Logic: 绑定当前操作与超时器；Constraints: 由 Promise 同步调用，不发送数据。 */
    function registerRequest(resolve, reject) {
      client.pending = {
        kind,
        expected,
        resolve,
        reject,
        started: performance.now(),
        timer: setTimeout(client.handleTimeout, TIMEOUT_MS),
      };
    }
    return new Promise(registerRequest);
  }
  /** Functionality: 发送应用级 ping。Inputs: 当前已打开 socket。Outputs: pong 的 RTT Promise。
   * Logic: 使用单调操作 id；Constraints: 连接必须 OPEN，不能与帧并发。 */
  ping() {
    if (this.socket?.readyState !== 1) throw new Error("连接尚未就绪");
    const id = String(++this.sequence);
    const result = this.wait("ping", id);
    try {
      this.socket.send(JSON.stringify({ type: "ping", id }));
    } catch (error) {
      this.fail(error);
    }
    return result;
  }
  /** Functionality: 发送一帧并验证原样返回。Inputs: JPEG ArrayBuffer。
   * Outputs: bytes/rttMs 的 Promise；Logic: 保存原字节，再发送二进制；Constraints: 1 MiB 上限。 */
  sendFrame(buffer) {
    if (this.socket?.readyState !== 1) throw new Error("连接尚未就绪");
    if (
      !(buffer instanceof ArrayBuffer) ||
      !buffer.byteLength ||
      buffer.byteLength > MAX_FRAME_BYTES
    ) {
      throw new Error("图像帧为空或超过 1 MiB 限制");
    }
    const result = this.wait("frame", new Uint8Array(buffer));
    try {
      this.socket.send(buffer);
    } catch (error) {
      this.fail(error);
    }
    return result;
  }
  /** Functionality: 完成握手。Inputs: socket open 事件隐式触发。Outputs: resolve。
   * Logic: 只接受 connect 等待状态；Constraints: 非预期握手视为协议错误。 */
  handleOpen() {
    if (this.pending?.kind !== "connect")
      return this.fail(new Error("非预期连接状态"));
    this.complete({});
  }
  /** Functionality: 校验响应。Inputs: WebSocket MessageEvent。Outputs: 完成请求或停止连接。
   * Logic: pong id 精确匹配；二进制长度及每个字节精确匹配。
   * Constraints: 不保存历史帧；原内容不一致时绝不显示为成功，不重试。 */
  handleMessage(event) {
    try {
      const pending = this.pending;
      if (!pending) throw new Error("收到非预期响应");
      const rttMs = performance.now() - pending.started;
      if (pending.kind === "ping") {
        const message = JSON.parse(event.data);
        if (
          message.type !== "pong" ||
          message.id !== pending.expected ||
          Object.keys(message).length !== 2
        ) {
          throw new Error("ping/pong 标识不匹配");
        }
        this.complete({ rttMs });
        return;
      }
      if (pending.kind !== "frame" || !(event.data instanceof ArrayBuffer)) {
        throw new Error("回传类型不匹配");
      }
      const received = new Uint8Array(event.data);
      if (received.length !== pending.expected.length)
        throw new Error("回传字节长度不一致");
      for (let index = 0; index < received.length; index += 1) {
        if (received[index] !== pending.expected[index])
          throw new Error("回传图像内容不一致");
      }
      this.complete({ bytes: event.data, rttMs });
    } catch (error) {
      this.fail(error);
    }
  }
  /** Functionality: 明确报告传输错误。Inputs: 浏览器 error 事件。Outputs: fail。
   * Logic: 浏览器不公开具体网络原因；Constraints: 提供可行动提示，不猜测后端错误。 */
  handleError() {
    this.fail(new Error("连接失败，请检查网络和服务端状态"));
  }
  /** Functionality: 处理关闭。Inputs: CloseEvent。Outputs: 拒绝在途请求并通知。
   * Logic: 用户 stop 已取消等待，非预期关闭保留 code；Constraints: 不自动重连。 */
  handleClose(event) {
    if (!this.stopped)
      this.fail(new Error(`连接已关闭（${event.code}），请重新点击开始`));
  }
  /** Functionality: 终止超时操作。Inputs: 在途计时器。Outputs: fail。
   * Logic: 10 秒内无匹配响应即失败；Constraints: 不发送替代帧或重放 ping。 */
  handleTimeout() {
    this.fail(new Error("连接或回传超时，请检查网络后重新开始"));
  }
  /** Functionality: 完成在途操作。Inputs: value。Outputs: resolve(value)。
   * Logic: 先清计时及 pending，再发布；Constraints: 调用者已确认 pending 存在。 */
  complete(value) {
    const pending = this.pending;
    this.pending = null;
    clearTimeout(pending.timer);
    pending.resolve(value);
  }
  /** Functionality: 发布错误并关闭。Inputs: Error。Outputs: reject 和一次断线回调。
   * Logic: 清计时、关闭活动 socket、去重通知；Constraints: 不打印数据正文。 */
  fail(error) {
    if (this.pending) {
      const pending = this.pending;
      this.pending = null;
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    if (this.socket?.readyState < 2) this.socket.close(4000, "echo_error");
    if (!this.stopped && !this.notified) {
      this.notified = true;
      this.onDisconnect(error);
    }
  }
  /** Functionality: 用户主动释放连接。Inputs: 当前 socket/pending。Outputs: 取消请求并关闭。
   * Logic: 先标记预期停止，避免触发错误提示；Constraints: 不清除已展示统计。 */
  stop() {
    this.stopped = true;
    if (this.pending) {
      const pending = this.pending;
      this.pending = null;
      clearTimeout(pending.timer);
      pending.reject(new Error("已停止"));
    }
    if (this.socket?.readyState < 2) this.socket.close(1000, "user_stop");
  }
}
