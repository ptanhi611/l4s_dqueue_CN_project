#pragma once
#include <cstdint>
#include <string>

// One AF_PACKET raw socket bound to one interface (for example r0-eth0).
// We get full Ethernet frames, and we send full Ethernet frames.
class RawSocket {
public:
    bool open_socket(const std::string& ifname);
    void close_socket();

    // Returns frame length, 0 if nothing is waiting, -1 on error.
    // Frames that we sent ourselves (PACKET_OUTGOING) are skipped.
    int receive_frame(uint8_t* buf, int max_len);

    bool send_frame(const uint8_t* buf, int len);

    int get_fd() const { return fd_; }
    const uint8_t* get_mac() const { return mac_; }

private:
    int fd_{-1};
    int ifindex_{0};
    uint8_t mac_[6]{};
};
