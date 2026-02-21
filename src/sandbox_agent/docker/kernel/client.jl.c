#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <errno.h>

#define SOCKET_PATH "/tmp/kernel.sock"
#define BUF_SIZE    65536
#define TIMEOUT_SEC 310

int main(void) {
    char tmp[BUF_SIZE];
    ssize_t n;

    /* ── Read all of stdin ─────────────────────────────── */
    char  *payload = NULL;
    size_t len = 0, cap = 0;

    while ((n = read(STDIN_FILENO, tmp, sizeof(tmp))) > 0) {
        if (len + (size_t)n > cap) {
            cap = (len + (size_t)n) * 2;
            if (cap < 4096) cap = 4096;
            payload = realloc(payload, cap);
            if (!payload) { perror("realloc"); return 1; }
        }
        memcpy(payload + len, tmp, (size_t)n);
        len += (size_t)n;
    }

    /* ── Connect to kernel via UNIX domain socket ──────── */
    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) { perror("socket"); return 1; }

    struct timeval tv = { .tv_sec = TIMEOUT_SEC };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect");
        close(sock);
        return 1;
    }

    /* ── Send payload + newline delimiter ──────────────── */
    size_t sent = 0;
    while (sent < len) {
        n = write(sock, payload + sent, len - sent);
        if (n <= 0) { perror("write"); close(sock); return 1; }
        sent += (size_t)n;
    }
    free(payload);

    char nl = '\n';
    if (write(sock, &nl, 1) != 1) { perror("write nl"); close(sock); return 1; }

    /* ── Read response until newline ───────────────────── */
    while ((n = read(sock, tmp, sizeof(tmp))) > 0) {
        char *eol = memchr(tmp, '\n', (size_t)n);
        size_t to_write = eol ? (size_t)(eol - tmp) : (size_t)n;
        size_t written = 0;
        while (written < to_write) {
            ssize_t w = write(STDOUT_FILENO, tmp + written, to_write - written);
            if (w <= 0) { perror("write stdout"); close(sock); return 1; }
            written += (size_t)w;
        }
        if (eol) break;
    }

    close(sock);
    return 0;
}
