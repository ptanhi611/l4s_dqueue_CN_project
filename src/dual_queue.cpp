#include "dual_queue.hpp"

DualQueue::DualQueue(uint32_t limit_bytes) : limit_bytes_(limit_bytes) {}

bool DualQueue::enqueue(const uint8_t* frame, int len, uint8_t ecn, bool to_l4s,
                        uint64_t flow_id, bool sanctioned) {

    // Shared buffer is full: tail drop
    if (bytes_ + len > limit_bytes_) return false;

    Packet p;
    p.data.assign(frame, frame + len);
    p.enqueue_time = Clock::now();
    p.ecn = ecn;
    p.flow_id = flow_id;
    p.sanctioned = sanctioned;

    if (to_l4s) l4s_q_.push_back(p);
    else classic_q_.push_back(p);

    bytes_ += len;
    return true;
}

Packet DualQueue::pop_classic() {
    Packet p = classic_q_.front();
    classic_q_.pop_front();
    bytes_ -= p.data.size();
    return p;
}

Packet DualQueue::pop_l4s() {
    Packet p = l4s_q_.front();
    l4s_q_.pop_front();
    bytes_ -= p.data.size();
    return p;
}

static double ms_since(Clock::time_point t, Clock::time_point now) {
    return std::chrono::duration<double, std::milli>(now - t).count();
}

double DualQueue::classic_delay_ms(Clock::time_point now) const {
    if (classic_q_.empty()) return 0.0;
    return ms_since(classic_q_.front().enqueue_time, now);
}

double DualQueue::l4s_delay_ms(Clock::time_point now) const {
    if (l4s_q_.empty()) return 0.0;
    return ms_since(l4s_q_.front().enqueue_time, now);
}

Clock::time_point DualQueue::classic_head_time() const {
    if (classic_q_.empty()) return Clock::now();
    return classic_q_.front().enqueue_time;
}
