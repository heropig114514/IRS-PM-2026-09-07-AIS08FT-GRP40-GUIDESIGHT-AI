"""Responsibilities: 验证 WebSocket 原样回传、控制契约、来源及容量边界。
Implementation: ApplicationCommunicator 执行真实 ASGI 处理器，不访问网络或摄像头。
Related Modules: core.echo；真实公网 TLS/WSS 另由联调验证。
Declaration Index:
- EchoTests — 协议与释放测试。
- EchoTests.open_socket — 创建带明确来源的 ASGI 测试会话。
- EchoTests.test_binary_is_unchanged — 二进制逐字节一致及断开释放。
- EchoTests.test_ping_id_is_preserved — 应用级 ping/pong id 一致。
- EchoTests.test_origin_and_host_rejected — 拒绝不同来源或主机。
- EchoTests.test_invalid_control_rejected — 拒绝无效控制消息。
- EchoTests.test_frame_limit — 拒绝大于上限及空消息。
- EchoTests.test_capacity_and_release — 第五个连接拒绝，已占连接在结束后释放。
- CameraPageTests — HTTP 页面及文件白名单。
- CameraPageTests.test_page_and_script — 页面与模块可获取且权限头明确。
- CameraPageTests.test_unknown_asset_is_not_exposed — 未列入资源的文件无法读取。
Variable Index: None。
"""

import json

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from . import echo


class EchoTests(SimpleTestCase):
    """Functionality: 检查 ASGI 回传协议及进程内容量状态。
    Logic: 使用真实处理器和内存 ASGI 队列；断开后等待处理器结束。
    Constraints: 仅单 worker，无实际网络或媒体设备。
    """

    async def open_socket(self, origin="http://127.0.0.1:8766", host="127.0.0.1:8766"):
        """Functionality: 准备测试会话并返回首个服务事件。
        Inputs: self、明确 origin 和 host；不替换服务器逻辑。
        Outputs: communicator 与首个响应消息；调用者必须结束已接受连接。
        Logic: 构造最小 websocket scope，然后发送 connect。
        Constraints: 假队列验证协议，不代表浏览器握手已成功。
        """
        communicator = ApplicationCommunicator(
            echo.echo_socket,
            {
                "type": "websocket",
                "path": "/ws/echo/",
                "headers": [(b"origin", origin.encode()), (b"host", host.encode())],
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        return communicator, await communicator.receive_output()

    async def test_binary_is_unchanged(self):
        """Functionality: 验证含所有字节值的内容完全一致。
        Inputs: 内存会话和合成二进制，不模拟 send/receive 处理器。
        Outputs: 断言内容、类型和连接计数；结束会话。
        Logic: 回传后主动断开并等待 finally 完成。
        Constraints: 数据不是真实摄像头 JPEG，不验证图像解码。
        """
        socket, accepted = await self.open_socket()
        self.assertEqual(accepted["type"], "websocket.accept")
        payload = bytes(range(256)) * 512
        await socket.send_input({"type": "websocket.receive", "bytes": payload})
        self.assertEqual(
            await socket.receive_output(), {"type": "websocket.send", "bytes": payload}
        )
        await socket.send_input({"type": "websocket.disconnect", "code": 1000})
        await socket.wait()
        self.assertEqual(echo.active_connections, 0)

    async def test_ping_id_is_preserved(self):
        """Functionality: 验证 pong 保留应用请求标识。
        Inputs: 合成 ping JSON 和真实 ASGI 处理器。
        Outputs: 匹配 pong；主动断开。
        Logic: 解码响应并核对完整字典。
        Constraints: 不测量真实网络延迟。
        """
        socket, _ = await self.open_socket()
        await socket.send_input(
            {"type": "websocket.receive", "text": '{"type":"ping","id":"test-1"}'}
        )
        response = await socket.receive_output()
        self.assertEqual(json.loads(response["text"]), {"type": "pong", "id": "test-1"})
        await socket.send_input({"type": "websocket.disconnect", "code": 1000})
        await socket.wait()

    async def test_origin_and_host_rejected(self):
        """Functionality: 验证来源及 Host 双重准入边界。
        Inputs: 错误 Origin 或 Host；无 mock。
        Outputs: 1008 关闭，连接计数保持 0。
        Logic: 未准入会话直接结束，不增加容量占用。
        Constraints: 同源验证不等于用户身份认证。
        """
        for origin, host in [
            ("https://other.example", "127.0.0.1:8766"),
            ("http://127.0.0.1:8766", "other.example"),
        ]:
            socket, response = await self.open_socket(origin, host)
            self.assertEqual(response, {"type": "websocket.close", "code": 1008})
            await socket.wait()
        self.assertEqual(echo.active_connections, 0)

    async def test_invalid_control_rejected(self):
        """Functionality: 无效 JSON 控制消息必须失败。
        Inputs: 三种无效消息；真实处理器，无 mock。
        Outputs: 每条会话均以 1008 结束。
        Logic: 覆盖 JSON 解析、对象类型、id 类型边界。
        Constraints: 不重试无效消息。
        """
        for text in ["not-json", "[]", '{"type":"ping","id":3}']:
            socket, _ = await self.open_socket()
            await socket.send_input({"type": "websocket.receive", "text": text})
            self.assertEqual((await socket.receive_output())["code"], 1008)
            await socket.wait()

    async def test_frame_limit(self):
        """Functionality: 拒绝空帧与超过 1 MiB 的帧。
        Inputs: 两个合成 bytes；保留实际 MAX_FRAME_BYTES。
        Outputs: 1009 关闭并释放连接。
        Logic: 直接验证处理器边界，而非修改阈值以通过测试。
        Constraints: ASGI 队列不验证 Uvicorn 的传输层大小限制。
        """
        for payload in [b"", b"x" * (echo.MAX_FRAME_BYTES + 1)]:
            socket, _ = await self.open_socket()
            await socket.send_input({"type": "websocket.receive", "bytes": payload})
            self.assertEqual((await socket.receive_output())["code"], 1009)
            await socket.wait()
        self.assertEqual(echo.active_connections, 0)

    async def test_capacity_and_release(self):
        """Functionality: 验证实际 4 连接上限及结束后的资源释放。
        Inputs: 并存的内存会话，未修改 MAX_CONNECTIONS。
        Outputs: 第五条 1013，最终计数为 0。
        Logic: finally 结束全部已接纳会话，避免测试污染进程状态。
        Constraints: 只适用单 worker，公网负载另受 systemd 限制。
        """
        sockets = []
        try:
            for _ in range(echo.MAX_CONNECTIONS):
                socket, response = await self.open_socket()
                sockets.append(socket)
                self.assertEqual(response["type"], "websocket.accept")
            rejected, response = await self.open_socket()
            self.assertEqual(response["code"], 1013)
            await rejected.wait()
        finally:
            for socket in sockets:
                await socket.send_input({"type": "websocket.disconnect", "code": 1000})
                await socket.wait()
        self.assertEqual(echo.active_connections, 0)


class CameraPageTests(SimpleTestCase):
    """Functionality: 验证公开页面与资源访问范围。
    Logic: Django 测试客户端读取实际前端文件，未替换文件系统。
    Constraints: 不执行浏览器脚本，也不申请摄像头权限。
    """

    def test_page_and_script(self):
        """Functionality: 页面及 JavaScript 文件具有正确响应。
        Inputs: self.client 与仓库前端文件。
        Outputs: 状态、摄像头权限头和 MIME 断言；关闭文件响应。
        Logic: 读取并关闭 FileResponse，避免文件句柄残留。
        Constraints: 不代表真实手机页面布局已验证。
        """
        for path, mime in [("/", "text/html"), ("/assets/camera.js", "text/javascript")]:
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertIn(mime, response["Content-Type"])
                self.assertEqual(response["Permissions-Policy"], "camera=(self), microphone=()")
                self.assertTrue(b"".join(response.streaming_content))
            finally:
                response.close()

    def test_unknown_asset_is_not_exposed(self):
        """Functionality: 拒绝白名单外的文件。
        Inputs: README.md 和 .env 资源路径；无 mock。
        Outputs: 两个 404；无文件写入。
        Logic: 在实际视图路径验证拒绝，不仅检查映射配置。
        Constraints: 只验证当前资源路由。
        """
        for path in ["/assets/README.md", "/assets/.env"]:
            self.assertEqual(self.client.get(path).status_code, 404)
