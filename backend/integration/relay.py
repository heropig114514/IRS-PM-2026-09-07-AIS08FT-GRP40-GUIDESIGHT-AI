"""Responsibilities: Relay one SSH-owned reverse loopback binding to the local web process.
Implementation: Fast bounded admission, owned worker/socket cleanup, raw TCP backpressure and
half-close; TCP_NODELAY on the local TCP peer; explicit failures without retry, credentials,
HTTP parsing or client ownership.
Related Modules: tools.run_phone_mvp owns SSH authentication, identity, keepalive and processes.
Declaration Index: RelayError; RelayError.__init__; _Connection; RelayForward;
RelayForward.__init__; RelayForward._fail; RelayForward._control; RelayForward._control.invoke;
RelayForward.start; RelayForward._incoming; RelayForward._accept_loop; RelayForward._forward;
RelayForward._pump; RelayForward._close_connection; RelayForward.check_active; RelayForward.run;
RelayForward.stop; RelayForward.active_connections.
Variable Index: LOOPBACK — both explicit TCP endpoints; BUFFER_BYTES — bounded per-direction
buffer; CONNECT_SECONDS/CONTROL_SECONDS/JOIN_SECONDS/POLL_SECONDS — transport lifecycle bounds;
logger — fixed codes/counts only; connections/threads — this instance's resources;
channel_progress/wait_seconds — drain writable partial SSH sends without an artificial poll delay;
slots/incoming — one shared queued+running connection bound; failure — first fatal code.
"""

import logging
import select
import socket
import threading
import time
from dataclasses import dataclass
from ipaddress import ip_address
from queue import Empty, Full, Queue

LOOPBACK = "127.0.0.1"
BUFFER_BYTES = 65536
CONNECT_SECONDS = 5
CONTROL_SECONDS = 10
JOIN_SECONDS = 6
POLL_SECONDS = 0.1
logger = logging.getLogger(__name__)


class RelayError(Exception):
    """Functionality: Expose a stable relay failure without private exception contents.
    Logic: Store the fixed code only. Constraints: no raw SSH/socket error or credential text.
    """

    def __init__(self, code):
        """Functionality: Initialize a safe failure. Inputs: fixed code. Outputs: exception.
        Logic: Use the code as its message. Constraints: callers supply no external error body.
        """
        self.code = code
        super().__init__(code)


@dataclass(eq=False)
class _Connection:
    """Functionality: Retain exactly one admitted channel and its owned local socket.
    Logic: Identity-based membership survives concurrent stop/worker completion.
    Constraints: no protocol bodies, destination choice, reusable PID or SSH-client ownership.
    """

    channel: object
    peer: object = None
    worker: object = None


class RelayForward:
    """Functionality: Own one fixed reverse SSH binding and bounded raw TCP connections.
    Logic: The SSH callback only admits/enqueues; separate workers forward both directions.
    Constraints: the authenticated transport must dedicate its port-forward handler to this
    instance; no other bind, SSH reconnect, web/GPU startup or closing the supplied SSH client.
    """

    def __init__(self, client, local_port=8766, remote_port=18766, max_connections=32,
                 stop_event=None):
        """Functionality: Configure an idle relay. Inputs: authenticated client/ports/cap/event.
        Outputs: idle resource owner. Logic: validate explicit ports and a positive connection cap.
        Constraints: both addresses stay numeric loopback; construction performs no I/O.
        """
        if any(type(port) is not int or not 1024 <= port <= 65535
               for port in (local_port, remote_port)):
            raise RelayError("invalid_relay_port")
        if type(max_connections) is not int or max_connections < 1:
            raise RelayError("invalid_connection_limit")
        self.client = client
        self.local_port, self.remote_port = local_port, remote_port
        self.max_connections = max_connections
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.transport = None
        self.failure = None
        self._started = self._bound = self._stopped = False
        self._lock, self._stop_lock = threading.Lock(), threading.Lock()
        self._connections, self._threads = set(), set()
        self._slots = threading.BoundedSemaphore(max_connections)
        self._incoming_queue = Queue(max_connections)
        self._accept_thread = None

    def _fail(self, code):
        """Functionality: Latch a fatal transport failure. Inputs: fixed code. Outputs: stop flag.
        Logic: Preserve the first failure and wake the owner. Constraints: never reconnect/retry.
        """
        with self._lock:
            if self.failure is None:
                self.failure = RelayError(code)
                logger.error("Relay failure code=%s", code)
        self.stop_event.set()

    def _control(self, function, *args, error_code, **kwargs):
        """Functionality: Bound one Paramiko control request. Inputs: trusted callable/arguments.
        Outputs: response or safe failure. Logic: wait once for its owned daemon control thread.
        Constraints: Paramiko global_request has no timeout; on a stalled request the caller must
        close its SSH client in its own finally block, which also releases any unconfirmed bind.
        """
        completed, outcome = threading.Event(), {}

        def invoke():
            """Functionality: Execute one control operation. Inputs: captured trusted call.
            Outputs: outcome/event. Logic: retain no external exception text.
            Constraints: no repeated request; owner closes SSH if this native wait stalls.
            """
            try:
                outcome["value"] = function(*args, **kwargs)
            except Exception:
                outcome["failed"] = True
            finally:
                completed.set()
                with self._lock:
                    self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=invoke, name="prs-relay-control", daemon=True)
        with self._lock:
            self._threads.add(thread)
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._threads.discard(thread)
            raise RelayError("relay_thread_failed") from None
        if not completed.wait(CONTROL_SECONDS):
            raise RelayError("relay_control_timeout")
        if outcome.get("failed"):
            raise RelayError(error_code)
        return outcome.get("value")

    def start(self):
        """Functionality: Request the single binding once. Inputs: configured SSH client.
        Outputs: self or RelayError. Logic: fixed loopback bind plus a bounded queue consumer.
        Constraints: refusal/occupied port fails; no port fallback or independent SSH connection.
        """
        if self._stopped or self.stop_event.is_set():
            raise RelayError("relay_stopped")
        if self._started:
            raise RelayError("relay_already_started")
        self._started = True
        try:
            self.transport = self.client.get_transport()
            available = self.transport is not None and self.transport.is_active()
        except Exception:
            available = False
        if not available:
            self._fail("ssh_unavailable")
            raise self.failure
        try:
            port = self._control(self.transport.request_port_forward, LOOPBACK, self.remote_port,
                                 handler=self._incoming, error_code="forward_request_failed")
            self._bound = True
            if type(port) is not int or port != self.remote_port:
                raise RelayError("forward_bind_mismatch")
            self._accept_thread = threading.Thread(
                target=self._accept_loop, name="prs-relay-accept", daemon=True)
            try:
                self._accept_thread.start()
            except RuntimeError:
                self._accept_thread = None
                raise RelayError("relay_thread_failed") from None
        except RelayError as error:
            self._fail(error.code)
            raise self.failure from None
        logger.info("Relay started local_port=%d remote_port=%d limit=%d",
                    self.local_port, self.remote_port, self.max_connections)
        return self

    def _incoming(self, channel, origin, destination):
        """Functionality: Admit forwarded channels without blocking SSH packet processing.
        Inputs: channel, server-side source and bind tuples. Outputs: queued work or closed channel.
        Logic: require nginx's loopback peer and exact owned destination; reserve before enqueue.
        Constraints: queued+running work is bounded; no connect/recv/send loop in this callback.
        """
        try:
            valid = (ip_address(origin[0]).is_loopback
                     and destination == (LOOPBACK, self.remote_port))
        except (ValueError, TypeError, IndexError):
            valid = False
        if not valid or self.stop_event.is_set() or not self._slots.acquire(blocking=False):
            channel.close()
            return
        connection = _Connection(channel)
        with self._lock:
            if self.stop_event.is_set():
                self._slots.release()
                channel.close()
                return
            self._connections.add(connection)
        try:
            self._incoming_queue.put_nowait(connection)
        except Full:
            self._close_connection(connection)

    def _accept_loop(self):
        """Functionality: Start owned forwarding workers outside Paramiko's transport thread.
        Inputs: bounded admission queue. Outputs: workers or fatal connection-state diagnostic.
        Logic: observe SSH liveness while waiting; workers own all local socket operations.
        Constraints: no retries, hidden reconnect or independent channel acceptance.
        """
        while not self.stop_event.is_set():
            if not self.transport.is_active():
                self._fail("ssh_disconnected")
                return
            try:
                connection = self._incoming_queue.get(timeout=POLL_SECONDS)
            except Empty:
                continue
            with self._lock:
                if connection not in self._connections or self.stop_event.is_set():
                    continue
                worker = threading.Thread(target=self._forward, args=(connection,),
                                          name="prs-relay-tcp", daemon=True)
                connection.worker = worker
                self._threads.add(worker)
                try:
                    worker.start()
                except RuntimeError:
                    self._threads.discard(worker)
                    connection.worker = None
                    worker = None
            if worker is None:
                self._close_connection(connection)
                self._fail("relay_thread_failed")
                return

    def _forward(self, connection):
        """Functionality: Connect exactly once to the local web listener and forward raw bytes.
        Inputs: admitted connection. Outputs: half-closed/closed owned endpoints.
        Logic: register before connect so stop can interrupt it; disable Nagle buffering on this
        owned TCP socket to forward small writes promptly; never inspect payloads.
        Constraints: a missing local service is fatal; peer resets end only that connection.
        """
        try:
            peer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with self._lock:
                if connection not in self._connections or self.stop_event.is_set():
                    peer.close()
                    return
                connection.peer = peer
            peer.settimeout(CONNECT_SECONDS)
            try:
                peer.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                if not self.stop_event.is_set():
                    self._fail("local_socket_setup_failed")
                return
            try:
                peer.connect((LOOPBACK, self.local_port))
            except OSError:
                if not self.stop_event.is_set():
                    self._fail("local_connect_failed")
                return
            peer.setblocking(False)
            connection.channel.settimeout(0.0)
            self._pump(connection.channel, peer)
        except (OSError, EOFError, ValueError):
            if not self.stop_event.is_set():
                logger.info("Relay connection ended code=peer_io_closed")
        finally:
            self._close_connection(connection)
            with self._lock:
                self._threads.discard(threading.current_thread())

    def _pump(self, channel, peer):
        """Functionality: Preserve full-duplex data and half-close under backpressure.
        Inputs: nonblocking SSH channel/socket. Outputs: no return body; endpoints remain owned.
        Logic: each direction buffers at most64KiB; channel send readiness is its SSH window,
        not its read-notification fileno; continue writable partial sends immediately after
        positive progress, retain bounded polling when blocked, and drain before propagating EOF.
        Constraints: no idle expiry, parsing, dropped bytes, artificial media pacing or retry.
        """
        to_peer, to_channel = bytearray(), bytearray()
        channel_open = peer_open = True
        peer_write_closed = channel_write_closed = False
        while not self.stop_event.is_set():
            channel_progress = False
            if to_channel and channel.send_ready():
                try:
                    count = channel.send(bytes(to_channel))
                    if count <= 0:
                        raise EOFError
                    del to_channel[:count]
                    channel_progress = True
                except (BlockingIOError, TimeoutError):
                    pass
            if not channel_open and not to_peer and not peer_write_closed:
                peer.shutdown(socket.SHUT_WR)
                peer_write_closed = True
            if not peer_open and not to_channel and not channel_write_closed:
                channel.shutdown_write()
                channel_write_closed = True
            if not channel_open and not peer_open and not to_peer and not to_channel:
                return
            readers = ([channel] if channel_open and len(to_peer) < BUFFER_BYTES else [])
            if peer_open and len(to_channel) < BUFFER_BYTES:
                readers.append(peer)
            writers = [peer] if to_peer else []
            wait_seconds = (0 if channel_progress and to_channel and channel.send_ready()
                            else POLL_SECONDS)
            if not readers and not writers:
                self.stop_event.wait(wait_seconds)
                continue
            readable, writable, _ = select.select(readers, writers, [], wait_seconds)
            if peer in writable:
                try:
                    count = peer.send(to_peer)
                    if count <= 0:
                        raise EOFError
                    del to_peer[:count]
                except (BlockingIOError, TimeoutError):
                    pass
            for endpoint in readable:
                target = to_peer if endpoint is channel else to_channel
                try:
                    chunk = endpoint.recv(BUFFER_BYTES - len(target))
                except (BlockingIOError, TimeoutError):
                    continue
                if chunk:
                    target.extend(chunk)
                elif endpoint is channel:
                    channel_open = False
                else:
                    peer_open = False

    def _close_connection(self, connection):
        """Functionality: Release one admitted slot and its owned endpoints exactly once.
        Inputs: connection identity. Outputs: closed socket/channel and removed ownership.
        Logic: detach under lock before close; repeated stop/worker-finally is harmless.
        Constraints: never close the SSH client, transport or another connection.
        """
        with self._lock:
            if connection not in self._connections:
                return
            self._connections.remove(connection)
        try:
            for endpoint in (connection.peer, connection.channel):
                if endpoint is not None:
                    try:
                        endpoint.close()
                    except Exception:
                        logger.warning("Relay endpoint close failed code=endpoint_close_failed")
        finally:
            self._slots.release()

    def check_active(self):
        """Functionality: Observe the running relay. Inputs: owned state. Outputs: bool/error.
        Logic: fatal latch wins; explicit stopping returnsFalse; dropped SSH becomes a fatal latch.
        Constraints: no remote request, local connection or automatic restart during observation.
        """
        if self.failure is not None:
            raise self.failure
        if self.stop_event.is_set() or self._stopped:
            return False
        if not self._started:
            raise RelayError("relay_not_started")
        if not self.transport.is_active():
            self._fail("ssh_disconnected")
            raise self.failure
        return True

    def run(self):
        """Functionality: Observe a previously started relay until explicit stop or failure.
        Inputs: existing instance. Outputs: normal completion or fixed RelayError.
        Logic: bounded polling and unconditional own cleanup. Constraints: start remains explicit.
        """
        try:
            while self.check_active():
                self.stop_event.wait(POLL_SECONDS)
        finally:
            self.stop()

    def stop(self):
        """Functionality: Cancel the owned bind and close/join owned resources once.
        Inputs: instance. Outputs: None or safe cleanup failure.
        Logic: seal admission, close sockets/channels, cancel exactly the acknowledged bind,
        then join workers under one deadline. Constraints: caller always closes its own SSH client
        in a separate finally; no remote pkill, other binding cancellation or repeated cancellation.
        """
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            self.stop_event.set()
            with self._lock:
                connections = list(self._connections)
            for connection in connections:
                self._close_connection(connection)
            error = None
            if self._bound:
                self._bound = False
                try:
                    self._control(self.transport.cancel_port_forward, LOOPBACK, self.remote_port,
                                  error_code="forward_cancel_failed")
                except RelayError as failure:
                    error = failure
            deadline = time.monotonic() + JOIN_SECONDS
            with self._lock:
                threads = list(self._threads)
            if self._accept_thread is not None:
                threads.append(self._accept_thread)
            for thread in threads:
                if thread is not threading.current_thread():
                    thread.join(max(0.0, deadline - time.monotonic()))
            if any(thread.is_alive() and thread is not threading.current_thread()
                   for thread in threads):
                error = error or RelayError("relay_cleanup_incomplete")
            logger.info("Relay stopped active_connections=%d", self.active_connections)
            if error is not None:
                self._fail(error.code)
                raise error

    @property
    def active_connections(self):
        """Functionality: Read owned queued+running count. Inputs: state. Outputs: integer.
        Logic: snapshot under lock. Constraints: no payload/coordinate logging or motor inference.
        """
        with self._lock:
            return len(self._connections)
