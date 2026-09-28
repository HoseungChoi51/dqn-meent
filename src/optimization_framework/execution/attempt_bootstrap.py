"""Installed prelude, run with isolated Python startup before captured imports."""
import array
import json
import os
import runpy
import socket
import sys


def main():
    directory, search_path, module, *arguments = sys.argv[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(30)
        connection.connect('/run/optimization-attempt.sock')
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            connection.sendmsg([b'R'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [descriptor]))])
        finally:
            os.close(descriptor)
        if connection.recv(1) != b'R':
            raise RuntimeError('The host did not prepare this bounded attempt')
    # Until this point, -I -S and the installed prelude prevent a capture from
    # shadowing standard-library modules used by resource setup or the handshake.
    sys.path[:0] = json.loads(search_path)
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
