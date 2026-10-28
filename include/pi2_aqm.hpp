#pragma once
#include <random>

struct AQMConfig{
    double target_c_ms     = 15.0;
    double target_l_ms     = 1.0;
    double time_update_ms  = 16.0;
    double alpha           = 0.16;
    double beta            = 3.20;
    double k_factor        = 2.0;
};

class PI2AQM{
public:

    // explicit prevents accidental implicit type conversions.
    explicit PI2AQM(const AQMConfig& config = AQMConfig());

    void update_control_loop(double current_qdelay_c);
    bool should_drop_classic(double qdelay_c);
    bool should_mark_l4s(double qdelay_l);

    double read_p_prime() const {return p_prime;}
    double read_p_c() const    {return p_c;}
    double read_p_cl() const   {return p_cl;}
    double read_p_l() const     {return p_l;}
    double read_e_prev() const  {return e_prev;}

private:

    AQMConfig config_;

    double p_prime{0.0};
    double p_c{0.0};
    double p_cl{0.0};
    double p_l{0.0};
    double e_prev{0.0};

    std::mt19937 rng_;
    std::uniform_real_distribution<double> dist_{0.0,1.0};
};