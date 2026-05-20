#!/usr/bin/env python3
import socket
import threading
import sys

WSL_PORT = 9223
WIN_PORT = 9222

def forward(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        src.close()
        dst.close()

def handle_client(client_sock):
    win_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        win_sock.connect(('127.0.0.1', WIN_PORT))
        t1 = threading.Thread(target=forward, args=(client_sock, win_sock))
        t2 = threading.Thread(target=forward, args=(win_sock, client_sock))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    except Exception as e:
        print(f'Proxy error: {e}', file=sys.stderr)
        client_sock.close()

if __name__ == '__main__':
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', WSL_PORT))
    server.listen(5)
    print(f'CDP proxy listening on port {WSL_PORT}, forwarding to Windows localhost:{WIN_PORT}')
    while True:
        client_sock, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(client_sock,))
        t.daemon = True
        t.start()
