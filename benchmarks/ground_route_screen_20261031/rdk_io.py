"""Fixed-key SSH helpers. Passwords are arguments in memory, never artifacts."""
import base64
import contextlib
import hashlib
import socket
import time

import paramiko

from device_settings import HOST, USER, EXPECTED_KEY, require_x5_identity
REMOTE = '/dev/shm/yuntong_ground_screen_20261031_round01'


@contextlib.contextmanager
def connect(password):
    require_x5_identity()
    connection = socket.create_connection((HOST, 22), timeout=5)
    transport = paramiko.Transport(connection)
    try:
        transport.start_client(timeout=8)
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(
            transport.get_remote_server_key().asbytes()).digest()).decode().rstrip('=')
        if fingerprint != EXPECTED_KEY:
            raise RuntimeError('Unexpected SSH host key')
        transport.auth_password(USER, password)
        yield transport
    finally:
        transport.close()


def run(transport, command, timeout=30):
    channel = transport.open_session()
    try:
        channel.exec_command(command)
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        while True:
            if channel.recv_ready():
                output.extend(channel.recv(65536))
            if channel.recv_stderr_ready():
                errors.extend(channel.recv_stderr(65536))
            if len(output) + len(errors) > 8 * 1024 * 1024:
                raise RuntimeError('Remote output exceeded bound')
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Remote command exceeded local timeout')
            time.sleep(.01)
        return dict(code=channel.recv_exit_status(), stdout=output.decode('utf-8', 'replace'),
                    stderr=errors.decode('utf-8', 'replace'))
    finally:
        channel.close()


def sftp(transport):
    return paramiko.SFTPClient.from_transport(transport)
