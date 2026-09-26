/* ============================================================
 * scripts/bench-cpu-monitor.c — 取某个进程的 CPU 时间（B4 实验专用）
 *
 * 为什么不直接用 `ps -o time=` / `ps -o %cpu`：
 *
 *   - `ps -o time=` 的精度是**秒**，一趟只跑几秒的实验误差 10% 以上
 *   - `ps -o %cpu` 是**生命周期平均值**，不是当前区间值，做差没有意义
 *   - macOS 的 `ps` 与 Linux 的 `ps` 时间格式不同（`MM:SS.ss` vs `[[DD-]HH:]MM:SS`），
 *     解析代码会在两个平台上不一致 —— 本项目的开发机是 macOS、
 *     目标是 openEuler，这种不一致正是最该避免的
 *
 * 本工具读 `/proc/<pid>/stat`（Linux）或 `proc_pid_rusage`（macOS），
 * 输出一个**高精度秒数**（user+sys），由调用方做差。
 *
 * 用法：
 *     bench-cpu-monitor <pid>
 * 输出：
 *     {"pid":N,"cpu_s":F}
 * 退出码：0 成功；2 参数错；3 取不到（进程不在 / 平台不支持）
 * ============================================================ */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__APPLE__)
#include <libproc.h>
#include <sys/resource.h>
#include <sys/types.h>
#else
#include <unistd.h>
#endif

static int read_cpu(pid_t pid, double *out) {
#if defined(__APPLE__)
    /* RUSAGE_INFO_V2 与 V4 都提供 ri_user_time / ri_system_time（纳秒）。 */
    struct rusage_info_v4 ru;
    memset(&ru, 0, sizeof(ru));
    if (proc_pid_rusage(pid, RUSAGE_INFO_V4, (rusage_info_t *)&ru) != 0) return -1;
    *out = ((double)ru.ri_user_time + (double)ru.ri_system_time) / 1e9;
    return 0;
#else
    /* /proc/<pid>/stat 的第 14/15 个字段是 utime/stime（单位：USER_HZ，通常 100） */
    char path[64];
    snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    FILE *fp = fopen(path, "r");
    if (fp == NULL) return -1;
    char buf[4096];
    size_t n = fread(buf, 1, sizeof(buf) - 1, fp);
    fclose(fp);
    buf[n] = '\0';
    /* 第 2 个字段是进程名，可能含空格与括号，必须从最后一个 ')' 之后开始切 */
    char *p = strrchr(buf, ')');
    if (p == NULL) return -1;
    p++;
    /* 此后字段编号：state=3, ppid=4, ... utime=14, stime=15
     * 从 p 开始把 state 当第 1 个字段数，utime 是第 12 个、stime 是第 13 个 */
    long long utime = 0, stime = 0;
    int field = 0;
    char *tok = strtok(p, " ");
    while (tok != NULL) {
        field++;
        if (field == 12) utime = atoll(tok);
        if (field == 13) stime = atoll(tok);
        if (field >= 13) break;
        tok = strtok(NULL, " ");
    }
    long hz = sysconf(_SC_CLK_TCK);
    if (hz <= 0) hz = 100;
    *out = ((double)utime + (double)stime) / (double)hz;
    return 0;
#endif
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <pid>\n", argv[0]);
        return 2;
    }
    pid_t pid = (pid_t)strtol(argv[1], NULL, 10);
    if (pid <= 0) {
        fprintf(stderr, "invalid pid: %s\n", argv[1]);
        return 2;
    }
    double cpu = 0.0;
    if (read_cpu(pid, &cpu) != 0) {
        fprintf(stderr, "cannot read cpu time of pid %ld\n", (long)pid);
        return 3;
    }
    printf("{\"pid\":%ld,\"cpu_s\":%.6f}\n", (long)pid, cpu);
    return 0;
}
