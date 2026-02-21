/*
 * Ephemeral TCP client for the R kernel.
 * Reads JSON from stdin, sends to the kernel on 127.0.0.1:8765,
 * writes the response to stdout and exits.
 *
 * Replaces client_r.py so Python is no longer needed in the R image.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>

#define KERNEL_PORT 8765
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

    /* ── Connect to kernel ─────────────────────────────── */
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) { perror("socket"); return 1; }

    struct timeval tv = { .tv_sec = TIMEOUT_SEC };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port   = htons(KERNEL_PORT),
    };
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect");
        close(sock);
        return 1;
    }

    /* ── Send payload, half-close write side ───────────── */
    size_t sent = 0;
    while (sent < len) {
        n = write(sock, payload + sent, len - sent);
        if (n <= 0) { perror("write"); close(sock); return 1; }
        sent += (size_t)n;
    }
    shutdown(sock, SHUT_WR);
    free(payload);

    /* ── Read response, write to stdout ────────────────── */
    while ((n = read(sock, tmp, sizeof(tmp))) > 0) {
        size_t written = 0;
        while (written < (size_t)n) {
            ssize_t w = write(STDOUT_FILENO, tmp + written, (size_t)n - written);
            if (w <= 0) { perror("write stdout"); close(sock); return 1; }
            written += (size_t)w;
        }
    }

    close(sock);
    return 0;
}
