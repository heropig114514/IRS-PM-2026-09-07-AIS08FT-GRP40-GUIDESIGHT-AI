"""Responsibilities: Verify bounded reverse-loopback relay lifetime without SSH credentials.
Implementation: Synthetic SSH control/channel adapters over real socket pairs and loopback TCP;
assert opaque binary preservation, TCP_NODELAY, half-close, overload, failure and owned cleanup.
Related Modules: integration.relay; tools.run_phone_mvp owns actual SSH/nginx deployment.
Declaration Index: SocketChannel; SocketChannel.__init__; SocketChannel.fileno;
SocketChannel.settimeout; SocketChannel.recv; SocketChannel.send; SocketChannel.send_ready;
SocketChannel.shutdown_write; SocketChannel.close; Transport; Transport.__init__;
Transport.is_active; Transport.request_port_forward; Transport.cancel_port_forward;
Transport.inject; Client; Client.__init__; Client.get_transport; Client.close;
receive_all; RelayTests; RelayTests.test_configuration_and_single_start;
RelayTests.test_refused_bind_fails_once_without_private_text;
RelayTests.test_binary_roundtrip_and_half_close;
RelayTests.test_binary_roundtrip_and_half_close.partial_send;
RelayTests.test_binary_roundtrip_and_half_close.observe_wait;
RelayTests.test_binary_roundtrip_and_half_close.serve; RelayTests.test_local_half_close_keeps_input;
RelayTests.test_owned_bounded_cleanup;
RelayTests.test_foreign_source_and_destination_rejected;
RelayTests.test_transport_loss_is_latched; RelayTests.test_local_connect_failure_is_fatal;
RelayTests.test_socket_configuration_failure_is_fatal;
RelayTests.test_cancel_failure_does_not_skip_endpoint_cleanup.
Variable Index: None; fixtures create no real SSH/SFTP connection, external network, files or GPU.
"""

import select
import socket
import threading
from unittest import TestCase
from unittest.mock import Mock, patch

from integration.relay import LOOPBACK, RelayError, RelayForward


class SocketChannel:
    """Functionality: Adapt a real stream socket to the used Paramiko Channel contract.
    Logic: retain native read/write/EOF behavior. Constraints: no simulated successful data copy.
    """

    def __init__(self, stream):
        """Functionality: Own one fixture stream. Inputs: socket. Outputs: channel adapter.
        Logic: preserve its fileno. Constraints: tests retain the other socket as the remote peer.
        """
        self.stream, self.closed = stream, False

    def fileno(self):
        """Functionality: Expose read readiness. Inputs: stream. Outputs: descriptor.
        Logic: native socket fileno. Constraints: fixture descriptor is not an SSH transport.
        """
        return self.stream.fileno()

    def settimeout(self, value):
        """Functionality: Apply explicit nonblocking mode. Inputs: timeout. Outputs: None.
        Logic: delegate once. Constraints: no implicit retries or timer-driven bytes.
        """
        self.stream.settimeout(value)

    def recv(self, count):
        """Functionality: Read real bytes. Inputs: bound. Outputs: bytes/EOF.
        Logic: native receive. Constraints: cannot create synthetic payload success.
        """
        return self.stream.recv(count)

    def send(self, data):
        """Functionality: Write real bytes. Inputs: data. Outputs: accepted count.
        Logic: native send. Constraints: caller handles partial writes.
        """
        return self.stream.send(data)

    def send_ready(self):
        """Functionality: Model SSH send-window availability. Inputs: stream. Outputs: bool.
        Logic: native write readiness. Constraints: production uses Channel.send_ready instead.
        """
        return bool(select.select([], [self.stream], [], 0)[1])

    def shutdown_write(self):
        """Functionality: Propagate half-close. Inputs: stream. Outputs: write EOF.
        Logic: native SHUT_WR. Constraints: reads remain possible.
        """
        self.stream.shutdown(socket.SHUT_WR)

    def close(self):
        """Functionality: Release this fixture endpoint. Inputs: state. Outputs: closed stream.
        Logic: idempotent socket close. Constraints: never closes the remote test endpoint.
        """
        self.closed = True
        self.stream.close()


class Transport:
    """Functionality: Model SSH control ownership and explicit incoming forwarding callbacks.
    Logic: record one requested bind and inject real stream channels.
    Constraints: no SSH server, authentication, process, external network or hidden reconnect.
    """

    def __init__(self):
        """Functionality: Initialize an authenticated fixture. Inputs: None. Outputs: idle control.
        Logic: no sockets created here. Constraints: bind/cancel failure must be explicit.
        """
        self.active, self.requests, self.cancels = True, [], []
        self.handler = None
        self.refuse = self.cancel_error = False

    def is_active(self):
        """Functionality: Observe connection state. Inputs: fixture. Outputs: bool.
        Logic: return explicit state. Constraints: not a real keepalive probe.
        """
        return self.active

    def request_port_forward(self, address, port, handler=None):
        """Functionality: Record one reverse binding. Inputs: fixed address/port/callback.
        Outputs: selected port or synthetic denial. Logic: no actual remote listener.
        Constraints: private sentinel must never escape the relay's public error.
        """
        self.requests.append((address, port))
        if self.refuse:
            raise RuntimeError("private-credential-sentinel")
        self.handler = handler
        return port

    def cancel_port_forward(self, address, port):
        """Functionality: Record only the cancelled bind. Inputs: address/port. Outputs: None.
        Logic: explicit optional failure. Constraints: cannot close the supplied client.
        """
        self.cancels.append((address, port))
        if self.cancel_error:
            raise RuntimeError("private-credential-sentinel")

    def inject(self, origin=(LOOPBACK, 30001), destination=(LOOPBACK, 18766)):
        """Functionality: Deliver a channel to the real callback. Inputs: explicit metadata.
        Outputs: remote socket/channel. Logic: callback receives one real socket-pair endpoint.
        Constraints: caller closes the returned remote endpoint; no server listener is fabricated.
        """
        stream, remote = socket.socketpair()
        remote.settimeout(3)
        channel = SocketChannel(stream)
        self.handler(channel, origin, destination)
        return remote, channel


class Client:
    """Functionality: Retain caller ownership of a synthetic authenticated SSH client.
    Logic: expose its transport and close count. Constraints: RelayForward must never close it.
    """

    def __init__(self, transport=None):
        """Functionality: Initialize owned fixture. Inputs: optional transport. Outputs: client.
        Logic: use the explicitly supplied object. Constraints: no credential discovery.
        """
        self.transport = transport if transport is not None else Transport()
        self.closed = False

    def get_transport(self):
        """Functionality: Expose the existing transport. Inputs: client. Outputs: transport.
        Logic: direct return. Constraints: no connection or reconnection.
        """
        return self.transport

    def close(self):
        """Functionality: Mark owner-only client cleanup. Inputs: fixture. Outputs: close flag.
        Logic: explicit state. Constraints: not called by the relay.
        """
        self.closed = True


def receive_all(stream):
    """Functionality: Read real bytes to EOF. Inputs: timed socket. Outputs: collected bytes.
    Logic: append native chunks. Constraints: fixture payloads are bounded; no retry on timeout.
    """
    output = bytearray()
    while data := stream.recv(8192):
        output.extend(data)
    return bytes(output)


class RelayTests(TestCase):
    """Functionality: Exercise forwarding and cleanup without changing system configuration.
    Logic: isolated loopback endpoints and explicit transport events.
    Constraints: not SSH-host, TLS, nginx, mobile-network or GPU validation.
    """

    def test_configuration_and_single_start(self):
        """Functionality: Reject invalid configuration and repeated startup.
        Inputs: ports/caps and one synthetic transport. Outputs: fixed bind/cancel, no client close.
        Logic: inspect exact ownership calls. Constraints: no port fallback or implicit start.
        """
        for values in ({"local_port": True}, {"remote_port": 0}, {"max_connections": 0}):
            with self.subTest(values=values), self.assertRaises(RelayError):
                RelayForward(Client(), **values)
        client = Client()
        relay = RelayForward(client)
        with self.assertRaisesRegex(RelayError, "relay_not_started"):
            relay.check_active()
        try:
            self.assertIs(relay.start(), relay)
            self.assertTrue(relay.check_active())
            with self.assertRaisesRegex(RelayError, "relay_already_started"):
                relay.start()
        finally:
            relay.stop()
        relay.stop()
        self.assertEqual(client.transport.requests, [(LOOPBACK, 18766)])
        self.assertEqual(client.transport.cancels, [(LOOPBACK, 18766)])
        self.assertFalse(client.closed)
        self.assertFalse(relay.check_active())

    def test_refused_bind_fails_once_without_private_text(self):
        """Functionality: Preserve safe denial diagnostics without trying another binding.
        Inputs: explicit SSH refusal. Outputs: fixed error, no retry/cancel/client close.
        Logic: count calls and inspect public text. Constraints: no raw exception leakage.
        """
        client = Client()
        client.transport.refuse = True
        relay = RelayForward(client)
        try:
            with self.assertRaises(RelayError) as raised:
                relay.start()
            self.assertEqual(raised.exception.code, "forward_request_failed")
            self.assertNotIn("sentinel", str(raised.exception))
        finally:
            relay.stop()
        self.assertEqual(client.transport.requests, [(LOOPBACK, 18766)])
        self.assertEqual(client.transport.cancels, [])
        self.assertFalse(client.closed)

    def test_binary_roundtrip_and_half_close(self):
        """Functionality: Preserve binary request/response larger than each bounded relay buffer.
        Inputs: real loopback TCP and synthetic SSH channel over a real socket pair.
        Outputs: exact bytes and EOF in both directions. Logic: response follows request half-close.
        Constraints: no HTTP parsing, manufactured receipt or unrestricted background server.
        """
        request, response = bytes(range(256)) * 800, bytes(range(255, -1, -1)) * 700
        native_select, writable_waits = select.select, []
        allow_response = threading.Event()

        def partial_send(channel, data):
            """Functionality: Exercise real partial channel writes smaller than the relay buffer.
            Inputs: channel and queued bytes. Outputs: actual socket send count.
            Logic: cap each native send at4096 bytes and remember remaining ready work.
            Constraints: does not synthesize progress or discard bytes.
            """
            count = channel.stream.send(data[:4096])
            channel.pending_after_send = len(data) - count
            return count

        def observe_wait(readers, writers, errors, timeout):
            """Functionality: Detect artificial sleeps while a partial SSH send remains writable.
            Inputs: real select arguments. Outputs: unchanged native readiness.
            Logic: record requested timeout only when remaining bytes can actually be written.
            Constraints: does not substitute timing, wake blocked sockets or bypass backpressure.
            """
            if (channel.stream not in writers and getattr(channel, "pending_after_send", 0)
                    and native_select([], [channel.stream], [], 0)[1]):
                writable_waits.append(timeout)
            return native_select(readers, writers, errors, timeout)

        with socket.socket() as listener:
            listener.bind((LOOPBACK, 0))
            listener.listen()
            listener.settimeout(3)
            client = Client()
            relay = RelayForward(client, local_port=listener.getsockname()[1]).start()
            remote, channel = client.transport.inject()
            observed, failures = [], []

            def serve():
                """Functionality: Read then respond over real loopback TCP.
                Inputs: test listener/request/response. Outputs: observed bytes or error type.
                Logic: run concurrently to exercise buffers larger than native socket capacity.
                Constraints: one finite connection, no independent service or synthesized receipt.
                """
                try:
                    with listener.accept()[0] as peer:
                        peer.settimeout(3)
                        observed.append(receive_all(peer))
                        if not allow_response.wait(3):
                            raise TimeoutError("Response observer was not installed")
                        peer.sendall(response)
                        peer.shutdown(socket.SHUT_WR)
                except Exception as error:
                    failures.append(type(error).__name__)

            server = threading.Thread(target=serve, daemon=True)
            server.start()
            try:
                remote.sendall(request)
                remote.shutdown(socket.SHUT_WR)
                with patch.object(SocketChannel, "send", partial_send), patch(
                    "integration.relay.select.select", side_effect=observe_wait
                ):
                    allow_response.set()
                    self.assertEqual(receive_all(remote), response)
            finally:
                remote.close()
                relay.stop()
                server.join(4)
            self.assertFalse(server.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(observed, [request])
            self.assertTrue(channel.closed)
            self.assertEqual(relay.active_connections, 0)
            self.assertFalse(client.closed)
            self.assertTrue(writable_waits)
            self.assertEqual(set(writable_waits), {0})

    def test_local_half_close_keeps_input(self):
        """Functionality: Keep incoming bytes valid after the local service finishes its output.
        Inputs: real TCP write EOF before remote request EOF. Outputs: both original payloads.
        Logic: verify the actual relay socket disables Nagle; local half-close maps only to
        SSH write EOF, leaving its request side readable.
        Constraints: no whole-connection shutdown that truncates the remaining opposite stream.
        """
        with socket.socket() as listener:
            listener.bind((LOOPBACK, 0))
            listener.listen()
            listener.settimeout(3)
            client = Client()
            relay = RelayForward(client, local_port=listener.getsockname()[1]).start()
            remote, _ = client.transport.inject()
            try:
                with listener.accept()[0] as peer:
                    peer.settimeout(3)
                    with relay._lock:
                        connection = next(iter(relay._connections))
                        self.assertEqual(
                            connection.peer.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY), 1
                        )
                    peer.sendall(b"response-first\x00\xff")
                    peer.shutdown(socket.SHUT_WR)
                    self.assertEqual(receive_all(remote), b"response-first\x00\xff")
                    remote.sendall(b"request-after-eof\x00\xff")
                    remote.shutdown(socket.SHUT_WR)
                    self.assertEqual(receive_all(peer), b"request-after-eof\x00\xff")
            finally:
                remote.close()
                relay.stop()

    def test_owned_bounded_cleanup(self):
        """Functionality: Bound admission and stop active read waits without touching the client.
        Inputs: capacity1, live TCP request and an excess incoming channel.
        Outputs: rejected excess, closed own endpoints, joined workers and one bind cancellation.
        Logic: overload is explicit EOF, not queued captured data or an SSH reconnect.
        Constraints: root remains the only SSH-client/process owner.
        """
        with socket.socket() as listener:
            listener.bind((LOOPBACK, 0))
            listener.listen()
            listener.settimeout(3)
            client = Client()
            relay = RelayForward(client, local_port=listener.getsockname()[1],
                                 max_connections=1).start()
            remote, channel = client.transport.inject()
            excess = None
            try:
                with listener.accept()[0] as peer:
                    excess, rejected = client.transport.inject()
                    self.assertEqual(excess.recv(1), b"")
                    self.assertTrue(rejected.closed)
                    self.assertEqual(relay.active_connections, 1)
                    relay.stop()
                    self.assertTrue(channel.closed)
                    peer.settimeout(3)
                    self.assertEqual(peer.recv(1), b"")
            finally:
                remote.close()
                if excess is not None:
                    excess.close()
                relay.stop()
            self.assertEqual(relay.active_connections, 0)
            self.assertFalse(relay._accept_thread.is_alive())
            self.assertFalse(client.closed)

    def test_foreign_source_and_destination_rejected(self):
        """Functionality: Refuse channels outside the exact reverse loopback binding.
        Inputs: foreign origin, foreign address and foreign destination port.
        Outputs: immediate EOF, no workers/local connection. Logic: inspect native endpoint state.
        Constraints: forwarded origin is nginx's server-side peer, never the phone's address.
        """
        client = Client()
        relay = RelayForward(client).start()
        try:
            for origin, destination in ((('192.0.2.1', 2), (LOOPBACK, 18766)),
                                        ((LOOPBACK, 2), ('0.0.0.0', 18766)),
                                        ((LOOPBACK, 2), (LOOPBACK, 18767))):
                with self.subTest(origin=origin, destination=destination):
                    remote, channel = client.transport.inject(origin, destination)
                    try:
                        self.assertTrue(channel.closed)
                        self.assertEqual(remote.recv(1), b"")
                    finally:
                        remote.close()
            self.assertEqual(relay.active_connections, 0)
            self.assertTrue(relay.check_active())
        finally:
            relay.stop()

    def test_transport_loss_is_latched(self):
        """Functionality: Fail one dead transport without recreating it.
        Inputs: explicit transport disconnect. Outputs: stable fatal code/stop flag.
        Logic: observation performs no new SSH request. Constraints: no automatic reconnect.
        """
        client = Client()
        relay = RelayForward(client).start()
        try:
            client.transport.active = False
            with self.assertRaises(RelayError) as raised:
                relay.check_active()
            self.assertEqual(raised.exception.code, "ssh_disconnected")
            self.assertTrue(relay.stop_event.is_set())
            self.assertEqual(len(client.transport.requests), 1)
        finally:
            relay.stop()

    def test_local_connect_failure_is_fatal(self):
        """Functionality: Surface a missing local web listener instead of retrying requests.
        Inputs: released loopback port and one forwarded channel. Outputs: fatal fixed code.
        Logic: await the worker's explicit stop signal. Constraints: no external socket or fallback.
        """
        with socket.socket() as reservation:
            reservation.bind((LOOPBACK, 0))
            port = reservation.getsockname()[1]
        client = Client()
        relay = RelayForward(client, local_port=port).start()
        remote, _ = client.transport.inject()
        try:
            self.assertTrue(relay.stop_event.wait(4))
            with self.assertRaises(RelayError) as raised:
                relay.check_active()
            self.assertEqual(raised.exception.code, "local_connect_failed")
        finally:
            remote.close()
            relay.stop()

    def test_socket_configuration_failure_is_fatal(self):
        """Functionality: Stop the relay when its explicit TCP configuration is refused.
        Inputs: Real channel pair and a refused socket option. Outputs: safe fatal code/cleanup.
        Logic: no local connect is attempted; owner closes endpoints after the worker fails.
        Constraints: no silent Nagle-enabled fallback, retry or private exception text.
        """
        stream, remote = socket.socketpair()
        channel = SocketChannel(stream)
        client = Client()
        relay = RelayForward(client).start()
        peer = Mock()
        peer.setsockopt.side_effect = OSError("private-credential-sentinel")
        try:
            with patch("integration.relay.socket.socket", return_value=peer):
                client.transport.handler(channel, (LOOPBACK, 30001), (LOOPBACK, 18766))
                self.assertTrue(relay.stop_event.wait(4))
                with self.assertRaises(RelayError) as raised:
                    relay.check_active()
                self.assertEqual(raised.exception.code, "local_socket_setup_failed")
                peer.connect.assert_not_called()
        finally:
            remote.close()
            relay.stop()
        self.assertTrue(channel.closed)
        peer.close.assert_called_once()
        self.assertEqual(relay.active_connections, 0)
        self.assertFalse(client.closed)

    def test_cancel_failure_does_not_skip_endpoint_cleanup(self):
        """Functionality: Keep cleanup safe when SSH cancellation is explicitly refused.
        Inputs: one admitted channel and a cancellation exception. Outputs: safe code/zero owners.
        Logic: endpoint cleanup precedes control cancellation. Constraints: root still closes SSH.
        """
        with socket.socket() as listener:
            listener.bind((LOOPBACK, 0))
            listener.listen()
            listener.settimeout(3)
            client = Client()
            relay = RelayForward(client, local_port=listener.getsockname()[1]).start()
            remote, channel = client.transport.inject()
            client.transport.cancel_error = True
            try:
                with listener.accept()[0] as peer:
                    peer.settimeout(3)
                    with self.assertRaises(RelayError) as raised:
                        relay.stop()
                    self.assertEqual(raised.exception.code, "forward_cancel_failed")
                    self.assertNotIn("sentinel", str(raised.exception))
                    self.assertTrue(channel.closed)
                    self.assertEqual(peer.recv(1), b"")
            finally:
                remote.close()
                relay.stop()
            self.assertEqual(relay.active_connections, 0)
            self.assertFalse(client.closed)
            self.assertEqual(client.transport.cancels, [(LOOPBACK, 18766)])
