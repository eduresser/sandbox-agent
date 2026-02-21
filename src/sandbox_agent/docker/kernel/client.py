import socket
import sys

SOCKET_PATH = "/tmp/kernel.sock"

payload = sys.stdin.buffer.read()

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(310)
sock.connect(SOCKET_PATH)
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
