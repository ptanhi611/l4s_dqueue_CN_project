#include <iostream>
#include "scheduler.hpp"

DualQScheduler::DualQScheduler(const SchedulerConfig& config ){
    config_ = config;
    l_bytes_served_ = 0;
}

SelectedQueue DualQScheduler::select_next_queue(bool c_empty,bool l_empty,std::chrono::steady_clock::time_point c_head_arrival){return SelectedQueue::NONE;}

void DualQScheduler::notify_packet_dispatched(SelectedQueue q, uint32_t bytes_sent) {}

void DualQScheduler::reset() {}