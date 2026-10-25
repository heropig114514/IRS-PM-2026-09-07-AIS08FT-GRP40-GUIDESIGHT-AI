/**
 * Responsibilities: Manage one WebSocket connection, ping/pong, exact single-frame byte validation, and explicit error semantics.
 * Implementation: Allow one in-flight operation; stop immediately on timeout, disconnect, or content mismatch without reconnect or retry.
 * Related Modules: camera.js, backend/core/echo.py, tests/echo-client.test.mjs。
 * Declaration Index: browserSocket; EchoClient; EchoClient.constructor; EchoClient.connect;
 * EchoClient.wait; EchoClient.wait.registerRequest; EchoClient.ping; EchoClient.sendFrame;
 * EchoClient.handleOpen; EchoClient.handleMessage; EchoClient.handleError; EchoClient.handleClose;
 * EchoClient.handleTimeout; EchoClient.complete; EchoClient.fail; EchoClient.stop。
 * Variable Index: TIMEOUT_MS — 10-second connect/echo timeout; MAX_FRAME_BYTES — 1 MiB input-frame limit.
 */
export const TIMEOUT_MS = 10000;
export const MAX_FRAME_BYTES = 1024 * 1024;

/** Functionality: Construct a browser WebSocket. Inputs: url. Outputs: New socket.
 * Logic: Use the native WebSocket; Constraints: Browser runtime only; tests replace it through explicit injection. */
function browserSocket(url) {
  return new WebSocket(url);
}

/** Functionality: Manage an echo connection with one verifiable request at a time.
 * Logic: pending stores operation kind, original bytes/ID, timing, and Promise callbacks; release it on completion or stop.
 * Constraints: Connect each instance once; do not send a frame and ping concurrently; no automatic reconnect. */
export class EchoClient {
  /** Functionality: Initialize a session. Inputs: url, onDisconnect callback, and injectable socketFactory.
   * Outputs: Instance; Logic: Store dependencies and bind events; Constraints: Do not connect or read media immediately. */
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
  /** Functionality: Start one connection. Inputs: Stored URL/factory. Outputs: Connection Promise.
   * Logic: Register the wait before binding events; Constraints: Reject duplicate connections and set binaryType to arraybuffer. */
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
  /** Functionality: Register the only in-flight operation. Inputs: kind and expected raw bytes or ping ID.
   * Outputs: Promise; Logic: Register callbacks and a 10-second timer; Constraints: Reject after stop or when pending already exists. */
  wait(kind, expected) {
    if (this.stopped || this.pending)
      throw new Error("连接已停止或已有数据等待返回");
    const client = this;
    /** Functionality: Store Promise callbacks. Inputs: resolve/reject. Outputs: None.
     * Logic: Bind the current operation and timeout; Constraints: Called synchronously by the Promise and sends no data. */
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
  /** Functionality: Send an application-level ping. Inputs: Current open socket. Outputs: Pong RTT Promise.
   * Logic: Use a monotonic operation ID; Constraints: Socket must be OPEN and no frame may be in flight. */
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
  /** Functionality: Send one frame and verify exact echo. Inputs: JPEG ArrayBuffer.
   * Outputs: Promise of bytes/rttMs; Logic: Retain original bytes, then send binary data; Constraints: 1 MiB limit. */
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
  /** Functionality: Complete the handshake. Inputs: Implicit socket open event. Outputs: resolve.
   * Logic: Accept only while connect is pending; Constraints: Treat unexpected handshake as a protocol error. */
  handleOpen() {
    if (this.pending?.kind !== "connect")
      return this.fail(new Error("非预期连接状态"));
    this.complete({});
  }
  /** Functionality: Validate a response. Inputs: WebSocket MessageEvent. Outputs: Complete request or stop the connection.
   * Logic: Match pong ID exactly; compare binary length and every byte.
   * Constraints: Do not retain frame history; never report mismatched bytes as success and do not retry. */
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
  /** Functionality: Report a transport error explicitly. Inputs: Browser error event. Outputs: fail.
   * Logic: Browsers do not expose the specific network cause; Constraints: Provide actionable guidance without guessing backend errors. */
  handleError() {
    this.fail(new Error("连接失败，请检查网络和服务端状态"));
  }
  /** Functionality: Handle closure. Inputs: CloseEvent. Outputs: Reject pending work and notify.
   * Logic: User stop has already cancelled its wait; retain close code for unexpected closure; Constraints: No automatic reconnect. */
  handleClose(event) {
    if (!this.stopped)
      this.fail(new Error(`连接已关闭（${event.code}），请重新点击开始`));
  }
  /** Functionality: Fail a timed-out operation. Inputs: In-flight timer. Outputs: fail.
   * Logic: Fail if no matching response arrives within 10 seconds; Constraints: Do not send substitute frames or replay pings. */
  handleTimeout() {
    this.fail(new Error("连接或回传超时，请检查网络后重新开始"));
  }
  /** Functionality: Complete the in-flight operation. Inputs: value. Outputs: resolve(value).
   * Logic: Clear timer and pending state before resolving; Constraints: Caller has already verified pending state. */
  complete(value) {
    const pending = this.pending;
    this.pending = null;
    clearTimeout(pending.timer);
    pending.resolve(value);
  }
  /** Functionality: Publish an error and close. Inputs: Error. Outputs: Reject and one disconnect callback.
   * Logic: Clear timer, close the active socket, and deduplicate notification; Constraints: Never log payload contents. */
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
  /** Functionality: Release the connection on user request. Inputs: Current socket/pending. Outputs: Cancel operation and close.
   * Logic: Mark expected stop first to suppress error status; Constraints: Preserve already displayed statistics. */
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
