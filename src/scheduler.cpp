#include <iostream>
#include "scheduler.hpp"

DualQScheduler::DualQScheduler(const SchedulerConfig& config ) : config_(config),l_bytes_served_(0) {}

SelectedQueue DualQScheduler::select_next_queue(bool c_empty,bool l_empty,std::chrono::steady_clock::time_point c_head_arrival) {

    // If both the queues are empty, nothing can be scheduled
    if(c_empty && l_empty) return SelectedQueue::NONE;

    // Work-conserving single-queue cases
    if(l_empty) return SelectedQueue::CLASSIC;
    if(c_empty) return SelectedQueue::L4S;

    // Both queues have data: measure head-of-line waiting time for Classic
    auto now = std::chrono::steady_clock::now();
    double wait_ms = std::chrono::duration<double, std::milli>(now-c_head_arrival).count();

    // Starvation circuit breaker overrides L-priority
    if (wait_ms > config_.classic_max_wait_ms)  return SelectedQueue::CLASSIC;

    // Latency priority within quantum budget
    if (l_bytes_served_ < config_.l_quantum_bytes)  return SelectedQueue::L4S;

    // Quantum exhausted: yield turn to Classic
    return SelectedQueue::CLASSIC;
}

void DualQScheduler::notify_packet_dispatched(SelectedQueue q, uint32_t bytes_sent) {

    if (q == SelectedQueue::CLASSIC) {
        l_bytes_served_ = 0;
    }
    else if(q == SelectedQueue::L4S) {
        l_bytes_served_ += bytes_sent;
    }
}

void DualQScheduler::reset() {
    l_bytes_served_ = 0;
}