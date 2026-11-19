#pragma once
#include <chrono>
#include <cstdint>
#include <deque>
#include <vector>

using Clock = std::chrono::steady_clock;

struct Packet {
    std::vector<uint8_t> data;       // full Ethernet frame
    Clock::time_point enqueue_time;  // for sojourn time (queue delay)
    uint8_t ecn = 0;
};

// Two FIFO queues (Classic and L4S) sharing one buffer limit in bytes,
// like RFC 9332 says (shared buffer, tail drop when full).
class DualQueue {
public:
    explicit DualQueue(uint32_t limit_bytes);

    // Returns false if the buffer is full (packet tail dropped)
    bool enqueue(const uint8_t* frame, int len, uint8_t ecn, bool to_l4s);

    Packet pop_classic();
    Packet pop_l4s();

    bool classic_empty() const { return classic_q_.empty(); }
    bool l4s_empty() const     { return l4s_q_.empty(); }
    bool both_empty() const    { return classic_q_.empty() && l4s_q_.empty(); }

    // Queue delay of the packet at the head, in ms (0 if queue is empty)
    double classic_delay_ms(Clock::time_point now) const;
    double l4s_delay_ms(Clock::time_point now) const;

    Clock::time_point classic_head_time() const;

    size_t classic_packets() const { return classic_q_.size(); }
    size_t l4s_packets() const     { return l4s_q_.size(); }
    uint32_t total_bytes() const   { return bytes_; }

private:
    std::deque<Packet> classic_q_;
    std::deque<Packet> l4s_q_;
    uint32_t limit_bytes_;
    uint32_t bytes_{0};
};
