"""Installed bwrap protocol relay. Copied as a standalone namespace launcher.

Only package pipes cross the Unix socket; no host file or directory descriptor
is sent. The owning host validates the complete invocation before launching.
"""
import array
import json
import os
import selectors
import socket
import struct
import sys


SOCKET = "/run/optimization-package.sock"
CHUNK = 65536
MAX_REQUEST = 1024 * 1024


def transfer(connection, descriptors):
    """Bound each pipe's pending bytes while preserving stdout/stderr separation."""
    incoming, outgoing, errors = descriptors
    pairs = {0: incoming, outgoing: 1, errors: 2}
    buffers = {destination: bytearray() for destination in pairs.values()}
    ended = set()
    status = bytearray()
    code = None
    for descriptor in {0, 1, 2, *descriptors}:
        os.set_blocking(descriptor, False)
    connection.setblocking(False)
    with selectors.DefaultSelector() as selector:
        while True:
            if code is not None and outgoing in ended and errors in ended and not buffers[1] and not buffers[2]:
                return code
            wanted = {}
            if code is None:
                wanted[connection.fileno()] = selectors.EVENT_READ
            for source, destination in pairs.items():
                if source not in ended and len(buffers[destination]) < CHUNK and (source != 0 or code is None):
                    wanted[source] = selectors.EVENT_READ
                if buffers[destination]:
                    wanted[destination] = selectors.EVENT_WRITE
                elif source in ended and destination == incoming and incoming not in ended:
                    os.close(incoming)
                    ended.add(incoming)
            for key in list(selector.get_map().values()):
                if key.fd not in wanted:
                    selector.unregister(key.fd)
            for descriptor, events in wanted.items():
                if descriptor not in selector.get_map():
                    selector.register(descriptor, events)
            for key, events in selector.select(.1):
                descriptor = key.fd
                if descriptor == connection.fileno():
                    data = connection.recv(4 - len(status))
                    if not data:
                        raise RuntimeError("Package supervisor disconnected before its exit receipt")
                    status.extend(data)
                    if len(status) == 4:
                        code = struct.unpack("!i", status)[0]
                        if code < 0:
                            code = 128 - code
                        ended.add(0)
                        buffers[incoming].clear()
                elif events & selectors.EVENT_READ:
                    destination = pairs[descriptor]
                    try:
                        data = os.read(descriptor, CHUNK - len(buffers[destination]))
                    except BlockingIOError:
                        continue
                    if data:
                        buffers[destination].extend(data)
                    else:
                        ended.add(descriptor)
                else:
                    try:
                        count = os.write(descriptor, buffers[descriptor])
                        del buffers[descriptor][:count]
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        if descriptor != incoming:
                            raise
                        buffers[incoming].clear()
                        ended.add(0)


def main():
    descriptors = array.array("i")
    request = json.dumps(sys.argv[1:], ensure_ascii=True).encode()
    if len(request) > MAX_REQUEST:
        raise ValueError("Package launch request exceeds its allowance")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(SOCKET)
        connection.sendall(struct.pack("!I", len(request)) + request)
        ready, ancillary, flags, _ = connection.recvmsg(1, socket.CMSG_SPACE(3 * descriptors.itemsize))
        try:
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors.frombytes(data[:len(data) - len(data) % descriptors.itemsize])
            if ready != b"R" or len(descriptors) != 3 or flags & socket.MSG_CTRUNC:
                raise RuntimeError("Package launch rejected by the execution host")
            return transfer(connection, descriptors)
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(126)
