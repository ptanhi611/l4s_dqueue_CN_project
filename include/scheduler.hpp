#pragma once

#include <chrono>
#include <cstdint>

// The queue which is scheduled
enum class SelectedQueue {
    CLASSIC,
    L4S,
    NONE
};

struct SchedulerConfig {
    uint32_t l_quantum_bytes    = 4500;  // ~3 MTU packets
    double classic_max_wait_ms  = 20.0;  // 20 ms starvation ceiling
};

class DualQScheduler {
public:
    explicit DualQScheduler(const SchedulerConfig& config = SchedulerConfig());

    // Evaluates queues and returns which one to dequeue next
    SelectedQueue select_next_queue(bool c_empty,bool l_empty,std::chrono::steady_clock::time_point c_head_arrival);

    // Updates deficit/credit byte tracking after transmission
    void notify_packet_dispatched(SelectedQueue q, uint32_t bytes_sent);

    // Optional reset helper to clear counters
    void reset();

private:
    SchedulerConfig config_;
    uint32_t l_bytes_served_{0};
};