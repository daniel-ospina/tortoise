/* tz_holder.c — deterministic holder of macOS's process-global timezone
 * rwlock, for the #3845 module-fork leak reproduction.
 *
 * It drives the lock exactly the way redis-server's own threads already do:
 *
 *   main thread, every event-loop wakeup:
 *     aeProcessEvents -> afterSleep -> localtime_r
 *     (redis 8.6.2 `_afterSleep+0x1b4` — an UNCONDITIONAL, straight-line call)
 *
 *   any thread, every timestamped log line:
 *     serverLogRaw -> strftime / strftime_l -> tzsetwall_basic
 *
 * Both reach `tzsetwall_basic -> _pthread_rwlock_lock_slow`.  The tz rwlock is
 * taken EXCLUSIVELY (the read path is the cheap one) whenever tzsetwall has to
 * re-read the zone file, and that re-read happens with the lock held — so the
 * window is file-I/O wide, not a few instructions.
 *
 * A spinning `localtime_r` is NOT enough: with an unchanged zone file the read
 * path never blocks a reader, and the measured hit rate was ~1 fork in 50-100.
 * So the holder points TZ at a zone file IT OWNS (`TORTOISE_TZ_FILE`, seeded
 * from /usr/share/zoneinfo) and bumps its mtime before every call.  Every
 * localtime_r/strftime in the whole process — the holder's, and redis's own
 * afterSleep and log timestamps — then re-reads that file, taking the
 * exclusive lock each time.  The collision is no longer a rare coin flip.
 *
 * The wedge itself is permanent by construction: `fork()` snapshots the rwlock
 * HELD, the holder thread does not exist in the child, and the child's own
 * first `RM_Log -> strftime_l` blocks on a lock nobody will ever release.
 *
 * The thread is created by a dyld constructor and sleeps first, so the server
 * can finish starting (the host process is launched with daemonize no for this
 * harness — a thread alive across redis-server's daemonize fork makes the
 * child's later dlopen of the Swift-bearing falkordb.so abort inside libobjc's
 * performForkChildInitialize, which would wedge the harness before the thing
 * under test ever runs).
 *
 * Inert unless TORTOISE_TZ_HOLDER=1.
 */
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

/* Bump the zone file's mtime so the next tzsetwall re-reads it (and holds the
 * exclusive lock for the whole re-read). */
static void bump(const char *path) {
    struct timeval tv[2];
    gettimeofday(&tv[0], NULL);
    tv[1] = tv[0];
    tv[0].tv_usec += 1; /* strictly newer than the previous bump */
    utimes(path, tv);
}

static void *spin(void *unused) {
    char buf[64];
    struct tm tm;
    const char *delay = getenv("TORTOISE_TZ_HOLDER_DELAY");
    const char *zone = getenv("TORTOISE_TZ_FILE");
    (void)unused;
    sleep(delay ? (unsigned)atoi(delay) : 3);
    for (;;) {
        if (zone != NULL) bump(zone);
        time_t t = time(NULL);
        localtime_r(&t, &tm);
        strftime(buf, sizeof(buf), "%d %b %Y %H:%M:%S", &tm);
    }
    return NULL;
}

__attribute__((constructor)) static void tz_holder_init(void) {
    const char *zone;
    int n, i;
    if (getenv("TORTOISE_TZ_HOLDER") == NULL) return;
    zone = getenv("TORTOISE_TZ_FILE");
    if (zone != NULL) {
        size_t len = strlen(zone) + 2;
        char *tz = malloc(len);
        if (tz != NULL) {
            tz[0] = ':';
            strcpy(tz + 1, zone);
            setenv("TZ", tz, 1);
            tzset();
        }
    }
    /* One thread already collides within a few dozen forks; the default is
     * several so the window is held by SOME thread for most of the wall clock
     * (redis really does log from more than one thread). */
    n = atoi(getenv("TORTOISE_TZ_THREADS") ? getenv("TORTOISE_TZ_THREADS") : "6");
    for (i = 0; i < n; i++) {
        pthread_t th;
        if (pthread_create(&th, NULL, spin, NULL) == 0) pthread_detach(th);
    }
}
