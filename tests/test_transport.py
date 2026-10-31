"""Run with python -m unittest discover -s tests -v.

Uses the supplied proxy unchanged on Linux. Other tests also run on Windows.
"""
import binascii
import collections
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("RTP-base", "RTP-opt")


def packet(kind, sequence, payload=b""):
    raw = struct.pack("!IIII", kind, sequence, len(payload), 0) + payload
    crc = binascii.crc32(raw) & 0xFFFFFFFF
    return raw[:12] + struct.pack("!I", crc) + payload


def unpack(raw):
    assert 16 <= len(raw) <= 1472, len(raw)
    kind, sequence, length, crc = struct.unpack("!IIII", raw[:16])
    assert length == len(raw) - 16
    assert binascii.crc32(raw[:12] + bytes(4) + raw[16:]) & 0xFFFFFFFF == crc
    return kind, sequence, raw[16:]


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.processes = []
        self.files = []

    def tearDown(self):
        for proc in self.processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        for handle in self.files:
            handle.close()

    def file(self):
        handle = tempfile.TemporaryFile()
        self.files.append(handle)
        return handle

    def spawn(self, args, **kwargs):
        errors = self.file()
        proc = subprocess.Popen([sys.executable, "-u", *map(str, args)], stderr=errors, **kwargs)
        proc.test_errors = errors
        self.processes.append(proc)
        return proc

    def ready(self, proc, port):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                proc.test_errors.seek(0)
                self.fail(proc.test_errors.read().decode(errors="replace"))
            # Observe the port without binding a probe that could race the child.
            if Path("/proc/net/udp").exists():
                entries = Path("/proc/net/udp").read_text().splitlines()[1:]
                if any(int(line.split()[1].split(":")[1], 16) == port for line in entries):
                    return
            else:
                lines = subprocess.check_output(["netstat", "-ano", "-p", "udp"], text=True).splitlines()
                if any(len(parts) == 4 and parts[1].endswith(":" + str(port))
                       and parts[-1] == str(proc.pid) for parts in map(str.split, lines)):
                    return
            time.sleep(.01)
        self.fail("Process did not bind UDP port")

    def receive(self, sock, timeout=3):
        sock.settimeout(timeout)
        raw, peer = sock.recvfrom(65535)
        return unpack(raw), peer

    def silent(self, sock):
        sock.settimeout(.12)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(65535)

    def receiver(self, folder, window):
        port = free_port()
        output = self.file()
        proc = self.spawn([ROOT / folder / "receiver.py", "127.0.0.1", port, window], stdout=output)
        # Wait for the START ACK, which also proves the receiver has bound its socket.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        peer = ("127.0.0.1", port)
        deadline = time.monotonic() + 10
        while True:
            sock.sendto(packet(0, 0), peer)
            try:
                ack, _ = self.receive(sock, .2)
                self.assertEqual(ack, (3, 1, b""))
                return proc, output, sock, peer
            except (socket.timeout, ConnectionResetError):
                if proc.poll() is not None or time.monotonic() >= deadline:
                    self.fail("Receiver did not acknowledge START")

    def test_receiver_protocol(self):
        for folder in FOLDERS:
            with self.subTest(folder=folder):
                proc, output, sock, peer = self.receiver(folder, 3)
                def exchange(raw, expected):
                    sock.sendto(raw, peer)
                    if expected is None:
                        self.silent(sock)
                    else:
                        self.assertEqual(self.receive(sock)[0], (3, expected, b""))
                exchange(packet(2, 3, b"c"), 1 if folder == "RTP-base" else 3)
                exchange(packet(2, 4, b"outside-window"), None)
                exchange(packet(2, 1, b"a")[:-1] + b"z", None)
                exchange(b"short", None)
                exchange(packet(2, 1, b"a") + b"bad-length", None)
                exchange(packet(0, 0), None)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
                    other.sendto(packet(2, 1, b"foreign"), peer)
                    self.silent(other)
                exchange(packet(2, 1, b"a"), 2 if folder == "RTP-base" else 1)
                exchange(packet(2, 2, b"b"), 4 if folder == "RTP-base" else 2)
                exchange(packet(2, 2, b"b"), 4 if folder == "RTP-base" else 2)
                exchange(packet(1, 4), 5)
                self.assertEqual(proc.wait(timeout=3), 0)
                output.seek(0)
                self.assertEqual(output.read(), b"abc")

    def transfer(self, folder, data, window, errors=None, seed=145):
        port = free_port()
        output = self.file()
        receiver = self.spawn([ROOT / folder / "receiver.py", "127.0.0.1", port, window], stdout=output)
        self.ready(receiver, port)
        target = port
        proxy = None
        if errors is not None:
            target = free_port()
            # Seed the standard random module before executing the ORIGINAL proxy.
            code = "import random,runpy,sys; sys.argv.pop(0); random.seed(int(sys.argv.pop(0))); runpy.run_path(sys.argv[0],run_name='__main__')"
            proxy = self.spawn(["-c", code, seed, ROOT / "test_scripts/proxy.py",
                                "127.0.0.1", target, "127.0.0.1", port, errors], stdout=self.file())
            self.ready(proxy, target)
        sender = self.spawn([ROOT / folder / "sender.py", "127.0.0.1", target, window],
                            stdin=subprocess.PIPE, stdout=self.file())
        try:
            sender.communicate(data, timeout=60)
            sender.test_errors.seek(0)
            self.assertEqual(sender.returncode, 0, sender.test_errors.read().decode(errors="replace"))
            try:
                self.assertEqual(receiver.wait(timeout=2), 0)
            except subprocess.TimeoutExpired:
                # The original proxy can drop END. The specification permits sender
                # to exit after 500 ms; receiver cannot exit until it receives END.
                self.assertIsNotNone(proxy, "Direct receiver did not exit after END")
            if proxy is not None:
                self.assertIsNone(proxy.poll(), "Original proxy exited unexpectedly")
            output.seek(0)
            self.assertEqual(output.read(), data)
        finally:
            for proc in (sender, receiver, proxy):
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_direct_binary_and_boundaries(self):
        for folder in FOLDERS:
            for size in (0, 1, 1455, 1456, 1457, 10240):
                with self.subTest(folder=folder, size=size):
                    data = (bytes(range(256)) * (size // 256 + 1))[:size]
                    self.transfer(folder, data, 3)

    def test_sender_retransmission_and_end_timeout(self):
        for folder in FOLDERS:
            with self.subTest(folder=folder):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.bind(("127.0.0.1", 0))
                    data = bytes(range(256)) * 17 + b"x" * 16  # Exactly three full DATA packets.
                    source = self.file()
                    source.write(data)
                    source.seek(0)
                    sender = self.spawn([ROOT / folder / "sender.py", "127.0.0.1", sock.getsockname()[1], 3],
                                        stdin=source, stdout=self.file())
                    first, peer = self.receive(sock, 10)
                    self.assertEqual(first, (0, 0, b""))
                    sock.sendto(packet(3, 1), peer)
                    counts = collections.Counter()
                    payloads = {}
                    for _ in range(3):
                        (kind, seq, body), _ = self.receive(sock)
                        self.assertEqual(kind, 2)
                        counts[seq] += 1
                        payloads[seq] = body
                    self.assertEqual(counts, {1: 1, 2: 1, 3: 1})
                    started = time.monotonic()
                    if folder == "RTP-opt":
                        for seq in (3, 2, 2):  # Reordered and duplicate selective ACKs.
                            time.sleep(.15)
                            sock.sendto(packet(3, seq), peer)
                    else:
                        for _ in range(3):
                            time.sleep(.15)
                            sock.sendto(packet(3, 1), peer)  # Duplicate cumulative ACKs must not reset timer.
                    # A corrupt ACK must not release the missing packet.
                    sock.sendto(packet(3, 4)[:-1] + b"x", peer)
                    expected_retries = 1 if folder == "RTP-opt" else 3
                    for i in range(expected_retries):
                        (kind, seq, body), _ = self.receive(sock)
                        if i == 0:
                            elapsed = time.monotonic() - started
                            self.assertGreaterEqual(elapsed, .4)
                            self.assertLess(elapsed, .85)
                        self.assertEqual(kind, 2)
                        self.assertEqual(body, payloads[seq])
                        counts[seq] += 1
                    self.assertEqual(counts, {1: 2, 2: 1, 3: 1} if folder == "RTP-opt" else {1: 2, 2: 2, 3: 2})
                    self.assertEqual(b"".join(payloads[n] for n in (1, 2, 3)), data)
                    sock.sendto(packet(3, 1 if folder == "RTP-opt" else 4), peer)
                    self.assertEqual(self.receive(sock)[0], (1, 4, b""))
                    ended = time.monotonic()
                    # Deliberately omit END ACK. Sender must exit after 500 ms.
                    self.assertEqual(sender.wait(timeout=2), 0)
                    self.assertGreaterEqual(time.monotonic() - ended, .4)

    @unittest.skipUnless(hasattr(socket, "MSG_DONTWAIT"), "Supplied proxy requires Linux MSG_DONTWAIT")
    def test_original_proxy(self):
        data = (ROOT / "test_scripts/test_message.txt").read_bytes()
        for folder in FOLDERS:
            for window in (1, 3, 128):
                for seed in (145, 146):
                    with self.subTest(folder=folder, window=window, seed=seed):
                        self.transfer(folder, data, window, "0123", seed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
