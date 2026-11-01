/* Pin measurement sockets to one link's address. The tunnel control channel is left alone. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

static int is_target(const struct sockaddr *addr) {
    const char *host = getenv("RTNP_BIND_HOST");
    char text[INET_ADDRSTRLEN];
    if (!host || !host[0] || addr == NULL || addr->sa_family != AF_INET) return 0;
    if (inet_ntop(AF_INET, &((const struct sockaddr_in *)addr)->sin_addr, text, sizeof text) == NULL) return 0;
    return strcmp(text, host) == 0;
}

static void pin(int fd) {
    const char *src = getenv("RTNP_BIND_SRC");
    const char *iface = getenv("RTNP_BIND_IFACE");
    struct sockaddr_in current;
    struct sockaddr_in local;
    socklen_t length = sizeof current;
    if ((!src || !src[0]) && (!iface || !iface[0])) return;
    memset(&current, 0, sizeof current);
    if (getsockname(fd, (struct sockaddr *)&current, &length) == 0 && current.sin_addr.s_addr != 0) return;
    /* A sender that bound its own port cannot be bound a second time, and every
       Rust UDP socket is bound before it connects. Steering by interface works
       on a bound socket and needs no privilege, so do that first. */
    if (iface && iface[0]) {
        unsigned int index = if_nametoindex(iface);
        if (index != 0) {
            uint32_t network_order = htonl(index);
            setsockopt(fd, IPPROTO_IP, IP_UNICAST_IF, &network_order, sizeof network_order);
        }
    }
    /* Tag measurement packets with DSCP EF (0xB8) so 802.11e WMM places them in
       the Voice queue (AC_VO) ahead of bulk TCP background transfers. */
    {
        int tos = 0xb8;
        setsockopt(fd, IPPROTO_IP, IP_TOS, &tos, sizeof tos);
    }
    if (src && src[0]) {
        memset(&local, 0, sizeof local);
        local.sin_family = AF_INET;
        if (inet_pton(AF_INET, src, &local.sin_addr) != 1) return;
        bind(fd, (struct sockaddr *)&local, sizeof local);
    }
}

int connect(int fd, const struct sockaddr *addr, socklen_t len) {
    static int (*real_connect)(int, const struct sockaddr *, socklen_t) = NULL;
    if (real_connect == NULL) real_connect = dlsym(RTLD_NEXT, "connect");
    if (is_target(addr)) pin(fd);
    return real_connect(fd, addr, len);
}

ssize_t sendto(int fd, const void *buf, size_t len, int flags, const struct sockaddr *addr, socklen_t addrlen) {
    static ssize_t (*real_sendto)(int, const void *, size_t, int, const struct sockaddr *, socklen_t) = NULL;
    if (real_sendto == NULL) real_sendto = dlsym(RTLD_NEXT, "sendto");
    if (is_target(addr)) pin(fd);
    return real_sendto(fd, buf, len, flags, addr, addrlen);
}

ssize_t sendmsg(int fd, const struct msghdr *message, int flags) {
    static ssize_t (*real_sendmsg)(int, const struct msghdr *, int) = NULL;
    if (real_sendmsg == NULL) real_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
    if (message != NULL && is_target(message->msg_name)) pin(fd);
    return real_sendmsg(fd, message, flags);
}
