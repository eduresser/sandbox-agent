/*
 * C kernel client: stdin → kernel → stdout.
 * Transport: KERNEL_SOCK (Unix socket) or KERNEL_PORT (TCP).
 * Protocol: half-close after payload, read until EOF.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>

#define BUF_SIZE    65536
#define TIMEOUT_SEC 310

static int connect_unix(const char *path) {
    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) return -1;

    struct timeval tv = {.tv_sec = TIMEOUT_SEC};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(sock);
        return -1;
    }
    return sock;
}

static int connect_tcp(const char *host, int port) {
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return -1;

    struct timeval tv = {.tv_sec = TIMEOUT_SEC};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons((uint16_t)port),
    };
    if (inet_pton(AF_INET, host, &addr.sin_addr) <= 0) {
        close(sock);
        return -1;
    }

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(sock);
        return -1;
    }
    return sock;
}

int main(void) {
    const char *sock_path = getenv("KERNEL_SOCK");
    const char *port_str = getenv("KERNEL_PORT");
    const char *host = getenv("KERNEL_HOST");
    if (!host) host = "127.0.0.1";

    int sock = -1;
    if (sock_path && sock_path[0]) {
        sock = connect_unix(sock_path);
    } else if (port_str && port_str[0]) {
        int port = atoi(port_str);
        if (port > 0 && port < 65536)
            sock = connect_tcp(host, port);
    }

    if (sock < 0) {
        if (sock_path && sock_path[0])
            perror("connect (unix)");
        else if (port_str && port_str[0])
            perror("connect (tcp)");
        else
            fputs("client: set KERNEL_SOCK or KERNEL_PORT\n", stderr);
        return 1;
    }

    /* ── Read all of stdin ─────────────────────────────── */
    char tmp[BUF_SIZE];
    ssize_t n;
    char *payload = NULL;
    size_t len = 0, cap = 0;

    while ((n = read(STDIN_FILENO, tmp, sizeof(tmp))) > 0) {
        if (len + (size_t)n > cap) {
            cap = (len + (size_t)n) * 2;
            if (cap < 4096) cap = 4096;
            payload = realloc(payload, cap);
            if (!payload) {
                perror("realloc");
                close(sock);
                return 1;
            }
        }
        memcpy(payload + len, tmp, (size_t)n);
        len += (size_t)n;
    }

    /* ── Send payload, half-close write side ───────────── */
    size_t sent = 0;
    while (sent < len) {
        n = write(sock, payload + sent, len - sent);
        if (n <= 0) {
            perror("write");
            free(payload);
            close(sock);
            return 1;
        }
        sent += (size_t)n;
    }
    free(payload);
    shutdown(sock, SHUT_WR);

    /* ── Read response, write to stdout ─────────────────── */
    while ((n = read(sock, tmp, sizeof(tmp))) > 0) {
        size_t written = 0;
        while (written < (size_t)n) {
            ssize_t w = write(STDOUT_FILENO, tmp + written, (size_t)n - written);
            if (w <= 0) {
                perror("write stdout");
                close(sock);
                return 1;
            }
            written += (size_t)w;
        }
    }

    close(sock);
    return 0;
}
