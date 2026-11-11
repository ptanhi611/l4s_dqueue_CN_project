#include "pi2_aqm.hpp"
#include <iostream>

PI2AQM::PI2AQM(const AQMConfig& config): config_(config){
    std::random_device rd;
    rng_.seed(rd());
}

void PI2AQM::update_control_loop(double current_qdelay_c){

    // Converting the delays to seconds from milliseconds
    double q = current_qdelay_c/1000.0;
    double target = config_.target_c_ms/1000.0;

    // calculating new probability
    double e = q-target;
    double p_delta = (config_.alpha * e) + (config_.beta*(e-e_prev));
    p_prime = p_prime + p_delta;
    if(p_prime < 0)p_prime = 0;
    if(p_prime > 1)p_prime = 1;
    e_prev = e;

    // calculating classic drop probability and coupled L4S drop probability
    p_c = (p_prime)*(p_prime);
    p_cl = (config_.k_factor * p_prime);
    if(p_cl > 1)p_cl = 1;
    p_l = p_cl;
}

// To silent the warning maybe_unused is used
bool PI2AQM::should_drop_classic([[maybe_unused]] double qdelay_c){

    // Randomly dropping a classical packet with classic drop probability
    double rnd_value = dist_(rng_);
    if(rnd_value < p_c)return true;
    return false;
}

bool PI2AQM::should_mark_l4s(double qdelay_l){
    double p_l_prime = 0.0;
    if(qdelay_l > config_.target_l_ms)p_l_prime = 1.0;

    p_l = (p_l_prime > p_cl) ? p_l_prime : p_cl;

    // Randomly Marking a low latency packet with coupled L4S drop probability
    double rnd_value = dist_(rng_);
    if(rnd_value < p_l)return true;
    return false;
}