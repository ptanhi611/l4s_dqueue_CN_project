#include "classifier.hpp"
#include <cstring>

// IPv4 header byte offsets (from start of IP header)
//   0: version (4 bits) + header length IHL (4 bits, in 32-bit words)
//   1: TOS  (DSCP 6 bits + ECN 2 bits)
//   8: TTL
//   9: protocol
//  10: header checksum (2 bytes)
//  12: source IP (4 bytes)
//  16: destination IP (4 bytes)

static int ip_header_len(const uint8_t* ip) {
    return (ip[0] & 0x0F) * 4;
}

PacketInfo parse_packet(const uint8_t* frame, int len) {
    PacketInfo info;

    if (len < ETH_HDR_LEN + 20) return info;  // too small to be IPv4

    // Ethertype 0x0800 = IPv4 (ARP, IPv6 etc. are ignored)
    if (frame[12] != 0x08 || frame[13] != 0x00) return info;

    const uint8_t* ip = frame + ETH_HDR_LEN;
    if ((ip[0] >> 4) != 4) return info;  // version must be 4

    info.is_ipv4 = true;
    info.ecn = ip[1] & 0x03;
    info.protocol = ip[9];
    memcpy(&info.src_ip, ip + 12, 4);
    memcpy(&info.dst_ip, ip + 16, 4);

    // RFC 9331: ECT(1) and CE go to the L4S queue, Not-ECT and ECT(0) go to Classic
    info.is_l4s = (info.ecn == ECN_ECT1 || info.ecn == ECN_CE);
    return info;
}

uint16_t ip_checksum(const uint8_t* ip_hdr, int hdr_len) {
    uint32_t sum = 0;

    // Add all 16-bit words
    for (int i = 0; i < hdr_len; i += 2) {
        uint16_t word = (ip_hdr[i] << 8) | ip_hdr[i + 1];
        sum += word;
    }

    // Fold carries back into 16 bits
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);

    return (uint16_t)(~sum);
}

static void fix_checksum(uint8_t* ip) {
    int hlen = ip_header_len(ip);
    ip[10] = 0;
    ip[11] = 0;
    uint16_t csum = ip_checksum(ip, hlen);
    ip[10] = csum >> 8;
    ip[11] = csum & 0xFF;
}

void set_ce_mark(uint8_t* frame) {
    uint8_t* ip = frame + ETH_HDR_LEN;
    ip[1] = ip[1] | 0x03;  // ECN bits -> 11
    // Only the IP checksum changes. TCP/UDP checksums do not cover the TOS byte.
    fix_checksum(ip);
}

bool decrement_ttl(uint8_t* frame) {
    uint8_t* ip = frame + ETH_HDR_LEN;
    if (ip[8] <= 1) return false;
    ip[8] = ip[8] - 1;
    fix_checksum(ip);
    return true;
}

void set_macs(uint8_t* frame, const uint8_t* src_mac, const uint8_t* dst_mac) {
    memcpy(frame, dst_mac, 6);      // bytes 0-5  : destination MAC
    memcpy(frame + 6, src_mac, 6);  // bytes 6-11 : source MAC
}
