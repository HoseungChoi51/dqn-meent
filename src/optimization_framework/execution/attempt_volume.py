"""A kernel-bounded private filesystem whose root is retained by the host.

Only the installed prelude runs before the root descriptor is transferred and
recovery data is seeded. Captured code never chooses the descriptor or quota.
The durable commit point is publication into the host-owned projection.
"""
import array
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import time


DEFAULT_PRIVATE_BYTES = 8 * 1024**3
DEFAULT_PRIVATE_ENTRIES = 100000
SCRATCH_BYTES = 64 * 1024**2


def descendant(pid, ancestor):
    for _ in range(16):
        if pid == ancestor:
            return True
        try:
            data = Path(f'/proc/{pid}/stat').read_text()
            pid = int(data[data.rfind(')') + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid <= 1:
            return False
    return False


def write_control(descriptor, value):
    """Replace only the control file, without following a worker-created link."""
    import secrets
    name = '.host-control-' + secrets.token_hex(16)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(name, 'control.json', src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(name, dir_fd=descriptor)
        except FileNotFoundError:
            pass


class AttemptVolume:
    def __init__(self, *, max_bytes=DEFAULT_PRIVATE_BYTES, max_entries=DEFAULT_PRIVATE_ENTRIES):
        if type(max_bytes) is not int or max_bytes < 1024**2 or max_bytes % 4096:
            raise ValueError('Private storage requires a page-aligned allowance of at least 1 MiB')
        if type(max_entries) is not int or max_entries < 100:
            raise ValueError('Private storage requires an entry allowance of at least 100')
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.fd = self.connection = self.temporary = self.listener = None

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='optimization-attempt-')
        self.socket_path = Path(self.temporary.name) / 'socket'
        self.bootstrap_path = Path(__file__).with_name('attempt_bootstrap.py')
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        self.listener.listen(1)
        self.listener.settimeout(.1)
        return self

    def receive(self, process, *, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ValueError('The isolated worker exited before preparing its private filesystem')
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid() or not descendant(pid, process.pid):
                connection.close()
                continue
            self.connection = connection
            self.listener.close()
            self.listener = None
            connection.settimeout(max(.05, deadline - time.monotonic()))
            descriptors = array.array('i')
            data, ancillary, flags, _ = connection.recvmsg(1, socket.CMSG_SPACE(4 * descriptors.itemsize))
            try:
                for level, kind, payload in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
                if data != b'R' or flags & socket.MSG_CTRUNC or len(descriptors) != 1:
                    raise ValueError('Invalid private-filesystem handshake')
                descriptor = descriptors[0]
                fs = os.fstatvfs(descriptor)
                if not stat.S_ISDIR(os.fstat(descriptor).st_mode) or fs.f_blocks * fs.f_frsize != self.max_bytes:
                    raise ValueError('Private filesystem differs from its host-owned byte allowance')
                os.set_inheritable(descriptor, False)
                self.fd = descriptor
                descriptors.pop()
                return descriptor
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)
        raise ValueError('Timed out preparing the bounded private filesystem')

    def ready(self):
        self.connection.sendall(b'R')
        self.connection.close()
        self.connection = None

    def usage(self):
        if self.fd is None:
            return None
        info = os.fstatvfs(self.fd)
        return {'kind': 'bounded_tmpfs', 'byte_limit': self.max_bytes,
                'bytes_used': (info.f_blocks - info.f_bfree) * info.f_frsize,
                'entry_limit': self.max_entries, 'entries_used': info.f_files - info.f_ffree,
                'entry_enforcement': 'host_watchdog', 'scratch_bytes_per_mount': SCRATCH_BYTES}

    def close(self):
        for name in ('connection', 'listener'):
            item = getattr(self, name)
            if item is not None:
                item.close()
                setattr(self, name, None)
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def __exit__(self, *_):
        self.close()
