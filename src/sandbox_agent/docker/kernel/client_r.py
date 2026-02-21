"""
Ephemeral client for the R kernel: reads JSON from stdin, sends to kernel
via TCP socket on localhost, prints response to stdout and exits.
"""

import socket
import sys

HOST = "127.0.0.1"
PORT = 8765

payload = sys.stdin.buffer.read()

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(310)
sock.connect((HOST, PORT))
sock.sendall(payload)
sock.shutdown(socket.SHUT_WR)

response = b""
while True:
    chunk = sock.recv(65536)
    if not chunk:
        break
    response += chunk

sock.close()
sys.stdout.buffer.write(response)
sys.stdout.buffer.flush()
