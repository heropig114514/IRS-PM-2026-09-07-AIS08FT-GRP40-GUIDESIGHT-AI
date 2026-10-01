/** Responsibilities: 用显式 socket mock 验证浏览器回传契约与取消行为。
 * Implementation: 原客户端逻辑配合受控事件，网络与摄像头完全未访问。
 * Related Modules: echo-client.js；公网联调独立执行。
 * Declaration Index: FakeSocket; FakeSocket.constructor; FakeSocket.send; FakeSocket.close;
 * FakeSocket.open; FakeSocket.receive; makeSession; makeSession.createSocket; makeSession.onDisconnect;
 * testBinary; testMismatch; testPing; testConcurrent; testConcurrent.sendConcurrentPing; testStop; testTimeout。
 * Variable Index: None。 */
import assert from "node:assert/strict";
import test from "node:test";
import { EchoClient } from "../echo-client.js";

/** Functionality: 显式网络替身。Logic: 仅保存发送数据和控制事件。
 * Constraints: 不等同真实 WebSocket，也不模拟浏览器权限。 */
class FakeSocket {
  /** Functionality: 初始化。Inputs: None。Outputs: 实例。
   * Logic: CONNECTING 状态；Constraints: 无网络请求。 */
  constructor() {
    this.readyState = 0;
    this.sent = [];
    this.closed = [];
  }
  /** Functionality: 保存消息。Inputs: data。Outputs: 无。
   * Logic: 记录引用；Constraints: 不传到服务器。 */
  send(data) {
    this.sent.push(data);
  }
  /** Functionality: 模拟关闭。Inputs: code、reason。Outputs: 保存关闭状态。
   * Logic: 不自动触发 onclose，以便分开测试错误通知；Constraints: 无网络副作用。 */
  close(code, reason) {
    this.readyState = 3;
    this.closed.push({ code, reason });
  }
  /** Functionality: 触发连接事件。Inputs: None。Outputs: onopen。
   * Logic: 状态变为 OPEN；Constraints: 由测试显式调用。 */
  open() {
    this.readyState = 1;
    this.onopen();
  }
  /** Functionality: 触发回传。Inputs: data。Outputs: onmessage。
   * Logic: 保留输入类型；Constraints: 无协议编码模拟。 */
  receive(data) {
    this.onmessage({ data });
  }
}

/** Functionality: 构造已打开的客户端。Inputs: None。Outputs: client/socket/errors。
 * Logic: 注入可控 socket 与错误收集；Constraints: 不使用原生 WebSocket。 */
async function makeSession() {
  const socket = new FakeSocket();
  const errors = [];
  /** Functionality: 提供唯一替身。Inputs: url。Outputs: socket。
   * Logic: 确认调用目标；Constraints: 无自动连接。 */
  function createSocket(url) {
    assert.equal(url, "ws://test/ws/echo/");
    return socket;
  }
  /** Functionality: 收集断线通知。Inputs: error。Outputs: errors 更新。
   * Logic: 保存异常引用；Constraints: 不吞掉被测 Promise 的拒绝。 */
  function onDisconnect(error) {
    errors.push(error);
  }
  const client = new EchoClient(
    "ws://test/ws/echo/",
    onDisconnect,
    createSocket,
  );
  const connected = client.connect();
  socket.open();
  await connected;
  return { client, socket, errors };
}

test("二进制回传逐字节一致" /** Functionality: 校验实际字节比较。Inputs: mock 会话。
 * Outputs: 断言 Promise 数据；Logic: 注入相同内容；Constraints: 无真实网络。 */, async function testBinary() {
  const { client, socket } = await makeSession();
  const bytes = new Uint8Array([0, 255, 17, 24]).buffer;
  const result = client.sendFrame(bytes);
  socket.receive(bytes.slice(0));
  assert.deepEqual(new Uint8Array((await result).bytes), new Uint8Array(bytes));
  client.stop();
});
test("不同内容失败且只通知一次" /** Functionality: 检查相同长度内容不一致。
 * Inputs: mock 响应。Outputs: 拒绝及关闭断言；Logic: 修改一个字节。
 * Constraints: 不把错误回传当作成功图像。 */, async function testMismatch() {
  const { client, socket, errors } = await makeSession();
  const result = client.sendFrame(new Uint8Array([1, 2]).buffer);
  socket.receive(new Uint8Array([1, 3]).buffer);
  await assert.rejects(result, /内容不一致/);
  socket.onclose({ code: 4000 });
  assert.equal(errors.length, 1);
  assert.equal(socket.closed[0].code, 4000);
  client.stop();
});
test("pong 标识不匹配明确失败" /** Functionality: 校验 ping id 边界。
 * Inputs: mock 和错误 id。Outputs: 拒绝断言；Logic: 假 pong 不能完成请求。
 * Constraints: 不测量真实网络延迟。 */, async function testPing() {
  const { client, socket } = await makeSession();
  const result = client.ping();
  socket.receive(JSON.stringify({ type: "pong", id: "wrong" }));
  await assert.rejects(result, /标识不匹配/);
  client.stop();
});
test("禁止并发在途请求" /** Functionality: 验证单帧背压。
 * Inputs: 在途帧与 ping。Outputs: 同步拒绝断言；Logic: 第一帧仍可正确完成。
 * Constraints: mock 不能证明真实带宽占用。 */, async function testConcurrent() {
  const { client, socket } = await makeSession();
  const bytes = new Uint8Array([7]).buffer;
  /** Functionality: 尝试并发 ping。Inputs: 已有在途帧的 client。Outputs: 原同步错误。
   * Logic: 直接调用被测 ping；Constraints: 不替换客户端状态。 */
  function sendConcurrentPing() {
    client.ping();
  }
  const result = client.sendFrame(bytes);
  assert.throws(sendConcurrentPing, /等待返回/);
  socket.receive(bytes.slice(0));
  await result;
  client.stop();
});
test("停止取消请求并关闭连接" /** Functionality: 验证用户停止语义。
 * Inputs: mock 在途帧。Outputs: 取消拒绝及 1000 关闭。
 * Logic: 主动停止不会发布断线错误；Constraints: 不验证真实设备轨道。 */, async function testStop() {
  const { client, socket, errors } = await makeSession();
  const result = client.sendFrame(new Uint8Array([7]).buffer);
  client.stop();
  await assert.rejects(result, /已停止/);
  assert.equal(socket.closed[0].code, 1000);
  assert.equal(errors.length, 0);
  assert.equal(client.pending, null);
});
test("超时处理器停止而不重试" /** Functionality: 检查超时故障语义。
 * Inputs: mock 在途 ping 和显式计时器回调。Outputs: 拒绝及一次发送断言。
 * Logic: 调用实际超时处理器；Constraints: 不验证 10 秒真实时钟调度。 */, async function testTimeout() {
  const { client, socket } = await makeSession();
  const result = client.ping();
  client.handleTimeout();
  await assert.rejects(result, /超时/);
  assert.equal(socket.sent.length, 1);
  client.stop();
});
