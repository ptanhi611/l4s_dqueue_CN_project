#pragma once
#include <chrono>
#include <cstdint>
#include <map>
#include <string>

#include "classifier.hpp"

using Clock = std::chrono::steady_clock;

// Queue protection for the L4S queue (our own addition, inspired by DOCSIS
// "Queue Protection" for low-latency queues).
//
// Problem: any sender can put ECT(1) on its packets and enter the L4S queue.
// A flow that ignores CE marks (for example a UDP flood) fills the L4S queue
// and destroys low latency for every well-behaved L4S flow.
//
// Idea: count how many bytes each flow has in the L4S queue right now.
// If one flow holds more than max_flow_bytes, that flow is "sanctioned" for
// sanction_ms: its packets are sent to the Classic queue instead, where the
// Classic AQM drops them (we drop instead of mark, because the flow already
// showed it does not react to marks).
// Well-behaved flows (DCTCP, the latency probe) keep only a small backlog,
// so they are never touched.

struct FlowState {
    uint32_t l4s_bytes = 0;               // bytes this flow has in the L4S queue now
    Clock::time_point sanctioned_until;    // while now < this, the flow uses the Classic queue
    uint32_t times_sanctioned = 0;
    uint64_t redirected_pkts = 0;
    std::string name;                      // for the summary, e.g. "10.0.1.1:40000 > 10.0.2.1:5203 udp"
};

class QueueProtection {
public:
    QueueProtection(uint32_t max_flow_bytes, double sanction_ms);

    // Call for every L4S packet before it is queued.
    // Returns true if it may use the L4S queue, false if it must go to Classic.
    bool allow_l4s(const PacketInfo& info, int len, Clock::time_point now);

    // Keep the per-flow byte count up to date
    void add_bytes(uint64_t flow_id, int len);     // after a packet entered the L4S queue
    void remove_bytes(uint64_t flow_id, int len);  // after a packet left the L4S queue

    void print_summary() const;

private:
    uint32_t max_flow_bytes_;
    Clock::duration sanction_time_;
    std::map<uint64_t, FlowState> flows_;
};
