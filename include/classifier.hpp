#pragma once
#include <cstdint>

// ECN codepoints = lowest 2 bits of the IPv4 TOS byte (RFC 3168, RFC 9331)
const uint8_t ECN_NOT_ECT = 0;  // 00
const uint8_t ECN_ECT1    = 1;  // 01  -> L4S
const uint8_t ECN_ECT0    = 2;  // 10  -> Classic ECN
const uint8_t ECN_CE      = 3;  // 11  -> Congestion Experienced

// Frame layout we use (no VLAN tags):
//   Ethernet header: 14 bytes, ethertype at bytes 12-13
//   IPv4 header starts at byte 14
const int ETH_HDR_LEN = 14;

struct PacketInfo {
    bool is_ipv4    = false;
    uint32_t src_ip = 0;   // network byte order
    uint32_t dst_ip = 0;   // network byte order
    uint8_t protocol = 0;  // 6 = TCP, 17 = UDP, 1 = ICMP
    uint8_t ecn     = 0;
    bool is_l4s     = false;  // true for ECT(1) and CE (RFC 9331 classifier)
};

PacketInfo parse_packet(const uint8_t* frame, int len);

// Set ECN bits to CE (11) and fix the IP header checksum.
// Only call this for ECN-capable packets (ECT(0), ECT(1)).
void set_ce_mark(uint8_t* frame);

// Normal router job: TTL - 1 and fix checksum. Returns false if TTL ran out.
bool decrement_ttl(uint8_t* frame);

// Rewrite Ethernet source and destination MAC for the next hop
void set_macs(uint8_t* frame, const uint8_t* src_mac, const uint8_t* dst_mac);

// Standard internet checksum over the IPv4 header
uint16_t ip_checksum(const uint8_t* ip_hdr, int hdr_len);
