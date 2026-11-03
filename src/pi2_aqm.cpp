#include "pi2_aqm.hpp"
#include <iostream>

PI2AQM::PI2AQM(const AQMConfig& config){}

void PI2AQM::update_control_loop(double current_qdelay_c){}

bool PI2AQM::should_drop_classic(double qdelay_c){return false;}

bool PI2AQM::should_mark_l4s(double qdelay_l){return false;}