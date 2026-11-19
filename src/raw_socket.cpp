#include "raw_socket.hpp"

#include <arpa/inet.h>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

bool RawSocket::open_socket(const std::string& ifname) {

    // ETH_P_ALL = give us every frame on the interface
    fd_ = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (fd_ < 0) {
        perror("socket (are you root?)");
        return false;
    }

    // Find the interface index and its MAC address
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname.c_str(), IFNAMSIZ - 1);

    if (ioctl(fd_, SIOCGIFINDEX, &ifr) < 0) {
        perror(("SIOCGIFINDEX " + ifname).c_str());
        return false;
    }
    ifindex_ = ifr.ifr_ifindex;

    if (ioctl(fd_, SIOCGIFHWADDR, &ifr) < 0) {
        perror(("SIOCGIFHWADDR " + ifname).c_str());
        return false;
    }
    memcpy(mac_, ifr.ifr_hwaddr.sa_data, 6);

    // Bind the socket to this interface only
    struct sockaddr_ll addr;
    memset(&addr, 0, sizeof(addr));
    addr.sll_family = AF_PACKET;
    addr.sll_protocol = htons(ETH_P_ALL);
    addr.sll_ifindex = ifindex_;
    if (bind(fd_, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind");
        return false;
    }

    // Promiscuous mode, so we see every frame on the wire
    struct packet_mreq mreq;
    memset(&mreq, 0, sizeof(mreq));
    mreq.mr_ifindex = ifindex_;
    mreq.mr_type = PACKET_MR_PROMISC;
    setsockopt(fd_, SOL_PACKET, PACKET_ADD_MEMBERSHIP, &mreq, sizeof(mreq));

    // Bigger kernel receive buffer, so bursts are not lost before we read them
    int rcvbuf = 4 * 1024 * 1024;
    setsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    // Non-blocking: receive_frame() returns 0 instead of waiting
    int flags = fcntl(fd_, F_GETFL, 0);
    fcntl(fd_, F_SETFL, flags | O_NONBLOCK);

    printf("Opened %s (ifindex %d, mac %02x:%02x:%02x:%02x:%02x:%02x)\n",
           ifname.c_str(), ifindex_, mac_[0], mac_[1], mac_[2], mac_[3], mac_[4], mac_[5]);
    return true;
}

void RawSocket::close_socket() {
    if (fd_ >= 0) close(fd_);
    fd_ = -1;
}

int RawSocket::receive_frame(uint8_t* buf, int max_len) {
    while (true) {
        struct sockaddr_ll from;
        socklen_t from_len = sizeof(from);
        int n = recvfrom(fd_, buf, max_len, 0, (struct sockaddr*)&from, &from_len);

        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;  // nothing waiting
            perror("recvfrom");
            return -1;
        }

        // A raw socket also sees the frames we send out. Skip those.
        if (from.sll_pkttype == PACKET_OUTGOING) continue;

        return n;
    }
}

bool RawSocket::send_frame(const uint8_t* buf, int len) {
    int sent = send(fd_, buf, len, 0);
    return sent == len;
}
