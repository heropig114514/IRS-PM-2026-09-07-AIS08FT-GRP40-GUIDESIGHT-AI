/** Responsibilities: Verify browser echo contracts and cancellation with an explicit socket mock.
 * Implementation: Exercise the actual client with controlled events; no network or camera is accessed.
 * Related Modules: echo-client.js; public-service integration is tested separately.
 * Declaration Index: MockSocket; MockSocket.constructor/send/close/open/message; fixture;
 * fixture.Socket; fixture.onError; testBinary; testMismatch; testPing; testConcurrent; testStop; testTimeout.
 * Variable Index: None; all socket state and error collections are test-local.
 * Declaration Index: FakeSocket; FakeSocket.constructor; FakeSocket.send; FakeSocket.close;
 * FakeSocket.open; FakeSocket.receive; makeSession; makeSession.createSocket; makeSession.onDisconnect;
 * testBinary; testMismatch; testPing; testConcurrent; testConcurrent.sendConcurrentPing; testStop; testTimeout。
 * Variable Index: None。 */
import assert from "node:assert/strict";
import test from "node:test";
import { EchoClient } from "../echo-client.js";

/** Functionality: Explicit network fixture. Logic: Store sent data and controlled events only.
 * Constraints: Not a real WebSocket and does not simulate browser permissions. */
class FakeSocket {
  /** Functionality: Initialize the socket fixture. Inputs: None. Outputs: Instance.
   * Logic: Start in CONNECTING state. Constraints: No network request. */
  constructor() {
    this.readyState = 0;
    this.sent = [];
    this.closed = [];
  }
  /** Functionality: Store a message. Inputs: data. Outputs: None.
   * Logic: Record the reference. Constraints: Do not send it to a server. */
  send(data) {
    this.sent.push(data);
  }
  /** Functionality: Simulate close. Inputs: code and reason. Outputs: Stored close state.
   * Logic: Do not trigger onclose automatically so error notification can be tested separately.
   * Constraints: No network side effects. */
  close(code, reason) {
    this.readyState = 3;
    this.closed.push({ code, reason });
  }
  /** Functionality: Trigger the open event. Inputs: None. Outputs: onopen.
   * Logic: Set state to OPEN. Constraints: Called explicitly by the test. */
  open() {
    this.readyState = 1;
    this.onopen();
  }
  /** Functionality: Trigger an echoed message. Inputs: data. Outputs: onmessage.
   * Logic: Preserve the input type. Constraints: Does not simulate protocol encoding. */
  receive(data) {
    this.onmessage({ data });
  }
}

/** Functionality: Build an opened client. Inputs: None. Outputs: client/socket/errors.
 * Logic: Inject a controlled socket and error collection. Constraints: Does not use native WebSocket. */
async function makeSession() {
  const socket = new FakeSocket();
  const errors = [];
  /** Functionality: Provide the sole socket fixture. Inputs: url. Outputs: Socket.
   * Logic: Record the connection target. Constraints: No automatic connection. */
  function createSocket(url) {
    assert.equal(url, "ws://test/ws/echo/");
    return socket;
  }
  /** Functionality: Collect disconnect notifications. Inputs: error. Outputs: Updated errors.
   * Logic: Save the error reference. Constraints: Do not swallow the tested Promise rejection. */
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

test("Binary echo matches byte for byte" /** Functionality: Verify actual byte comparison. Inputs: Mock session.
 * Outputs: Promise data assertions. Logic: Inject identical content. Constraints: No real network. */, async function testBinary() {
  const { client, socket } = await makeSession();
  const bytes = new Uint8Array([0, 255, 17, 24]).buffer;
  const result = client.sendFrame(bytes);
  socket.receive(bytes.slice(0));
  assert.deepEqual(new Uint8Array((await result).bytes), new Uint8Array(bytes));
  client.stop();
});
test("Different content fails and notifies once" /** Functionality: Check mismatch for content of equal length.
 * Inputs: Mock response. Outputs: Rejection and close assertions. Logic: Change one byte.
 * Constraints: Do not treat an erroneous echo as a successful image. */, async function testMismatch() {
  const { client, socket, errors } = await makeSession();
  const result = client.sendFrame(new Uint8Array([1, 2]).buffer);
  socket.receive(new Uint8Array([1, 3]).buffer);
  await assert.rejects(result, /内容不一致/);
  socket.onclose({ code: 4000 });
  assert.equal(errors.length, 1);
  assert.equal(socket.closed[0].code, 4000);
  client.stop();
});
test("Mismatched pong identifier fails explicitly" /** Functionality: Verify the ping ID boundary.
 * Inputs: Mock and incorrect ID. Outputs: Rejection assertion. Logic: A false pong cannot complete the request.
 * Constraints: Does not measure real network latency. */, async function testPing() {
  const { client, socket } = await makeSession();
  const result = client.ping();
  socket.receive(JSON.stringify({ type: "pong", id: "wrong" }));
  await assert.rejects(result, /标识不匹配/);
  client.stop();
});
test("Reject concurrent in-flight requests" /** Functionality: Verify single-frame backpressure.
 * Inputs: In-flight frame and ping. Outputs: Synchronous rejection; the first frame can still complete.
 * Constraints: Mock does not prove real bandwidth usage. */, async function testConcurrent() {
  const { client, socket } = await makeSession();
  const bytes = new Uint8Array([7]).buffer;
  /** Functionality: Attempt a concurrent ping. Inputs: Client with an in-flight frame. Outputs: Original synchronous error.
   * Logic: Call the tested ping directly. Constraints: Do not replace client state. */
  function sendConcurrentPing() {
    client.ping();
  }
  const result = client.sendFrame(bytes);
  assert.throws(sendConcurrentPing, /等待返回/);
  socket.receive(bytes.slice(0));
  await result;
  client.stop();
});
test("Stop cancels the request and closes the connection" /** Functionality: Verify user-stop semantics.
 * Inputs: Mock in-flight frame. Outputs: Cancellation rejection and close code 1000.
 * Logic: Explicit stop does not publish a disconnect error. Constraints: Does not verify real device tracks. */, async function testStop() {
  const { client, socket, errors } = await makeSession();
  const result = client.sendFrame(new Uint8Array([7]).buffer);
  client.stop();
  await assert.rejects(result, /已停止/);
  assert.equal(socket.closed[0].code, 1000);
  assert.equal(errors.length, 0);
  assert.equal(client.pending, null);
});
test("Timeout handler stops without retrying" /** Functionality: Check timeout failure semantics.
 * Inputs: Mock in-flight ping and explicit timer callback. Outputs: Rejection and one-send assertion.
 * Logic: Call the actual timeout handler. Constraints: Does not verify a real 10-second timer. */, async function testTimeout() {
  const { client, socket } = await makeSession();
  const result = client.ping();
  client.handleTimeout();
  await assert.rejects(result, /超时/);
  assert.equal(socket.sent.length, 1);
  client.stop();
});
