/* ============================================================
 * scripts/bench-loadgen.c — 极简 HTTP 压测客户端（B4 实验专用）
 *
 * 为什么不用 Python 客户端，也不用 `ab`
 * -------------------------------------
 * docs/11 §1.6 的教训：**压测工具本身会污染结论**。
 * 同一服务、同一端点 /healthz、同为 c=20：
 *
 *     ab -k                          3592 req/s
 *     Python httpx async 客户端       332 req/s      ← 差 10.8 倍
 *
 * Python 客户端在 c=20 时就自己饱和了，用它测「服务端 CPU/请求」
 * 会把客户端的开销混进结论。所以本实验用一个**原生进程**做客户端，
 * 并且**单独测量客户端自己的 CPU**（见 scripts/bench-cpu-monitor.c）——
 * 如果客户端 CPU 接近主机核数，那这组数据就不可信，必须报出来而不是藏起来。
 *
 * 也不直接用 `ab` 的原因：`ab` 不输出**客户端自身**的 CPU 时间，
 * 我们无法验证「工具没有成为瓶颈」这个前提。
 *
 * 功能刻意做到最少：
 *   - 支持 keep-alive（`-k`）与每次新建连接（`-c`）两种模式
 *   - N 个线程各跑 M 次请求
 *   - 统计完成数 / 错误数 / 墙钟 / 客户端 CPU（user+sys）
 *   - 输出一行 JSON，便于脚本侧解析
 *
 * 编译：
 *     cc -O2 -pthread -o bench-loadgen scripts/bench-loadgen.c
 *
 * 用法：
 *     ./bench-loadgen <host> <port> <path> <并发> <每线程请求数> <keepalive 0|1>
 *
 * 输出（stdout 一行）：
 *     {"completed":N,"errors":N,"elapsed_s":F,"client_cpu_s":F,
 *      "client_wall_s":F,"cpu_cores":F,"rps":F}
 * ============================================================ */

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define REQ_BUF 2048
#define RSP_BUF 8192

typedef struct {
    const char *host;
    int port;
    const char *path;
    int per_thread;
    int keepalive;
    long completed;
    long errors;
    long bytes;
} worker_arg;

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static double cpu_seconds(void) {
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    return (double)ru.ru_utime.tv_sec + (double)ru.ru_utime.tv_usec / 1e6 +
           (double)ru.ru_stime.tv_sec + (double)ru.ru_stime.tv_usec / 1e6;
}

/* 读掉一个 HTTP 响应。先找到 \r\n\r\n，再按 Content-Length 读完 body。
 * 我们只关心「请求确实被服务端完整处理了」，不解析内容。
 *
 * 不用 strcasestr：它不是可移植接口（glibc 需要 _GNU_SOURCE，
 * musl 上根本没有）。这里手写逐行扫描，行为可控且两个平台一致。 */
static int read_response(int fd, int keepalive) {
    (void)keepalive;
    char buf[RSP_BUF];
    int total = 0;
    int header_end = -1;
    long content_length = -1;
    int chunked = 0;
    for (;;) {
        ssize_t n = recv(fd, buf + total, (int)sizeof(buf) - 1 - total, 0);
        if (n <= 0) {
            /* 对端关闭：_base_clause 里没有长连接复用，
             * 头部已读全时按成功算（`Connection: close` 的正常收尾）。 */
            return (header_end >= 0) ? 0 : -1;
        }
        total += (int)n;
        buf[total] = '\0';
        if (header_end < 0) {
            char *p = strstr(buf, "\r\n\r\n");
            if (p != NULL) {
                header_end = (int)(p - buf) + 4;
                /* 逐行扫头部，找 Content-Length / Transfer-Encoding */
                char *line = buf;
                while (line < p) {
                    char *eol = strstr(line, "\r\n");
                    if (eol == NULL || eol > p) break;
                    if (strncasecmp(line, "content-length:", 15) == 0)
                        content_length = strtol(line + 15, NULL, 10);
                    else if (strncasecmp(line, "transfer-encoding:", 18) == 0 &&
                             strncasecmp(line + 18, " chunked", 8) == 0)
                        chunked = 1;
                    line = eol + 2;
                }
            }
        }
        if (header_end >= 0) {
            if (content_length >= 0) {
                if (total - header_end >= content_length) return 0;
            } else if (!chunked) {
                /* 没有 body（204 等）：头部读完即可 */
                return 0;
            } else {
                /* chunked：找到结束块 "0\r\n\r\n" */
                if (strstr(buf + header_end, "0\r\n\r\n") != NULL) return 0;
            }
        }
        if (total >= (int)sizeof(buf) - 1) return 0; /* 防爆缓冲 */
    }
}

static int connect_to(const char *host, int port) {
    char portstr[16];
    struct addrinfo hints, *res = NULL, *rp = NULL;
    snprintf(portstr, sizeof(portstr), "%d", port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, portstr, &hints, &res) != 0) return -1;
    int fd = -1;
    for (rp = res; rp != NULL; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return -1;
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    return fd;
}

static int build_request(char *buf, size_t cap, const char *host, int port,
                         const char *path, int keepalive) {
    return snprintf(buf, cap,
                    "GET %s HTTP/1.1\r\n"
                    "Host: %s:%d\r\n"
                    "User-Agent: localcraft-bench-loadgen/1\r\n"
                    "Accept: */*\r\n"
                    "Connection: %s\r\n"
                    "\r\n",
                    path, host, port, keepalive ? "keep-alive" : "close");
}

static void *worker(void *raw) {
    worker_arg *a = (worker_arg *)raw;
    char req[REQ_BUF];
    int reqlen = build_request(req, sizeof(req), a->host, a->port, a->path, a->keepalive);

    /* 错开起始时刻：非 keep-alive 模式下每请求都要 connect/close，
     * 若所有线程同时建连会在 accept 队列上打出一个尖峰，
     * 那个尖峰是压测工具的产物而不是被测量的服务行为。
     * 用线程号做微秒级抖动把它摊平（docs/11 §1.6 的同一条教训）。 */
    struct timespec jitter;
    jitter.tv_sec = 0;
    jitter.tv_nsec = ((long)(uintptr_t)pthread_self() % 500) * 1000; /* 0~0.5 ms */
    nanosleep(&jitter, NULL);

    int fd = -1;
    if (a->keepalive) {
        fd = connect_to(a->host, a->port);
        if (fd < 0) {
            a->errors += a->per_thread;
            return NULL;
        }
    }
    for (int i = 0; i < a->per_thread; i++) {
        if (!a->keepalive) {
            fd = connect_to(a->host, a->port);
            if (fd < 0) { a->errors++; continue; }
        }
        ssize_t sent = 0;
        int ok = 1;
        while (sent < reqlen) {
            ssize_t n = send(fd, req + sent, (size_t)(reqlen - sent), 0);
            if (n <= 0) { ok = 0; break; }
            sent += n;
        }
        if (ok && read_response(fd, a->keepalive) == 0) {
            a->completed++;
        } else {
            a->errors++;
            if (a->keepalive) {
                /* keep-alive 连接坏了：重连一次继续，否则整个线程都会失败 */
                close(fd);
                fd = connect_to(a->host, a->port);
                if (fd < 0) {
                    a->errors += a->per_thread - i - 1;
                    break;
                }
            }
        }
        if (!a->keepalive) close(fd);
    }
    if (a->keepalive && fd >= 0) close(fd);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 7) {
        fprintf(stderr,
                "usage: %s <host> <port> <path> <concurrency> <per_thread> <keepalive 0|1>\n",
                argv[0]);
        return 2;
    }
    const char *host = argv[1];
    int port = atoi(argv[2]);
    const char *path = argv[3];
    int concurrency = atoi(argv[4]);
    int per_thread = atoi(argv[5]);
    int keepalive = atoi(argv[6]) ? 1 : 0;
    if (concurrency <= 0 || per_thread <= 0) {
        fprintf(stderr, "concurrency and per_thread must be > 0\n");
        return 2;
    }

    pthread_t *threads = calloc((size_t)concurrency, sizeof(pthread_t));
    worker_arg *args = calloc((size_t)concurrency, sizeof(worker_arg));
    if (!threads || !args) return 2;
    for (int i = 0; i < concurrency; i++) {
        args[i].host = host;
        args[i].port = port;
        args[i].path = path;
        args[i].per_thread = per_thread;
        args[i].keepalive = keepalive;
    }

    double cpu0 = cpu_seconds();
    double t0 = now_seconds();
    for (int i = 0; i < concurrency; i++)
        pthread_create(&threads[i], NULL, worker, &args[i]);
    long completed = 0, errors = 0;
    for (int i = 0; i < concurrency; i++) {
        pthread_join(threads[i], NULL);
        completed += args[i].completed;
        errors += args[i].errors;
    }
    double elapsed = now_seconds() - t0;
    double cpu = cpu_seconds() - cpu0;

    printf("{\"completed\":%ld,\"errors\":%ld,\"elapsed_s\":%.4f,"
           "\"client_cpu_s\":%.4f,\"cpu_cores\":%.3f,\"rps\":%.1f}\n",
           completed, errors, elapsed, cpu, elapsed > 0 ? cpu / elapsed : 0.0,
           elapsed > 0 ? (double)completed / elapsed : 0.0);
    free(threads);
    free(args);
    return (errors > 0 && completed == 0) ? 1 : 0;
}
