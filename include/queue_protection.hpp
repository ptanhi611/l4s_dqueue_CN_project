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
//
// Optional policing (mode dualq_qp_police): a sanctioned flow may also send at
// most police_rate into the Classic queue (token bucket); the rest is dropped.
// Without it, an unresponsive flood moved to Classic keeps the PI2 controller in
// overload, and the coupled marking then pushes the good L4S flows down too.

struct FlowState {
    uint32_t l4s_bytes = 0;               // bytes this flow has in the L4S queue now
    Clock::time_point sanctioned_until;    // while now < this, the flow uses the Classic queue
    uint32_t times_sanctioned = 0;
    uint64_t redirected_pkts = 0;
    uint64_t policed_pkts = 0;             // dropped by the rate limit while sanctioned
    double tokens = 0;                     // token bucket (bytes) for policing
    Clock::time_point last_refill;
    std::string name;                      // for the summary, e.g. "10.0.1.1:40000 > 10.0.2.1:5203 udp"
};

class QueueProtection {
public:
    // police_rate_bps = 0 means no policing (only redirect to Classic)
    QueueProtection(uint32_t max_flow_bytes, double sanction_ms, double police_rate_bps = 0);

    // Call for every L4S packet before it is queued.
    // Returns true if it may use the L4S queue, false if it must go to Classic.
    bool allow_l4s(const PacketInfo& info, int len, Clock::time_point now);

    // Keep the per-flow byte count up to date
    void add_bytes(uint64_t flow_id, int len);     // after a packet entered the L4S queue
    void remove_bytes(uint64_t flow_id, int len);  // after a packet left the L4S queue

    // For a packet of a sanctioned flow: true = may enter the Classic queue,
    // false = over the rate limit, drop it
    bool police(uint64_t flow_id, int len, Clock::time_point now);

    void print_summary() const;

private:
    uint32_t max_flow_bytes_;
    Clock::duration sanction_time_;
    double police_rate_bps_;
    std::map<uint64_t, FlowState> flows_;
};
