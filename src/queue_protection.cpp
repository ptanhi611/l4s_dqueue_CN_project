#include "queue_protection.hpp"

#include <arpa/inet.h>
#include <cstdio>

QueueProtection::QueueProtection(uint32_t max_flow_bytes, double sanction_ms, double police_rate_bps)
    : max_flow_bytes_(max_flow_bytes), police_rate_bps_(police_rate_bps) {
    sanction_time_ = std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double, std::milli>(sanction_ms));
}

static std::string flow_name(const PacketInfo& info) {
    char src[INET_ADDRSTRLEN], dst[INET_ADDRSTRLEN], text[128];
    inet_ntop(AF_INET, &info.src_ip, src, sizeof(src));
    inet_ntop(AF_INET, &info.dst_ip, dst, sizeof(dst));
    const char* proto = (info.protocol == 6) ? "tcp" : (info.protocol == 17) ? "udp" : "other";
    snprintf(text, sizeof(text), "%s:%u > %s:%u %s", src, info.src_port, dst, info.dst_port, proto);
    return text;
}

bool QueueProtection::allow_l4s(const PacketInfo& info, int len, Clock::time_point now) {
    FlowState& flow = flows_[info.flow_id];
    if (flow.name.empty()) flow.name = flow_name(info);

    // Already sanctioned: keep sending it to Classic until the sanction ends
    if (now < flow.sanctioned_until) {
        flow.redirected_pkts++;
        return false;
    }

    // This packet would make the flow hold too much of the L4S queue: sanction it
    if (flow.l4s_bytes + len > max_flow_bytes_) {
        flow.sanctioned_until = now + sanction_time_;
        flow.times_sanctioned++;
        flow.redirected_pkts++;
        return false;
    }

    return true;
}

void QueueProtection::add_bytes(uint64_t flow_id, int len) {
    flows_[flow_id].l4s_bytes += len;
}

void QueueProtection::remove_bytes(uint64_t flow_id, int len) {
    FlowState& flow = flows_[flow_id];
    if (flow.l4s_bytes >= (uint32_t)len) flow.l4s_bytes -= len;
    else flow.l4s_bytes = 0;
}

bool QueueProtection::police(uint64_t flow_id, int len, Clock::time_point now) {
    if (police_rate_bps_ <= 0) return true;  // policing switched off

    FlowState& flow = flows_[flow_id];
    const double bucket_size = 15000;        // bytes, allows a small burst (~10 packets)

    // Refill the bucket for the time since the last packet
    if (flow.last_refill.time_since_epoch().count() == 0) {
        flow.tokens = bucket_size;           // first packet of this flow
    } else {
        double seconds = std::chrono::duration<double>(now - flow.last_refill).count();
        flow.tokens += seconds * police_rate_bps_ / 8.0;
        if (flow.tokens > bucket_size) flow.tokens = bucket_size;
    }
    flow.last_refill = now;

    if (flow.tokens >= len) {
        flow.tokens -= len;
        return true;
    }
    flow.policed_pkts++;
    return false;
}

void QueueProtection::print_summary() const {
    printf("queue protection: %zu L4S flows seen\n", flows_.size());
    for (const auto& item : flows_) {
        const FlowState& flow = item.second;
        if (flow.times_sanctioned == 0) continue;
        printf("  sanctioned %u times, %lu packets moved to Classic, %lu dropped by rate limit: %s\n",
               flow.times_sanctioned, (unsigned long)flow.redirected_pkts,
               (unsigned long)flow.policed_pkts, flow.name.c_str());
    }
}
