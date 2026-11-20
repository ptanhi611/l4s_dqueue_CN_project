// Real-time simulation of the router's queue/AQM decisions (same logic as main.cpp step 1 + 3 + 4)
#include <cstdio>
#include <cstring>
#include <string>
#include <arpa/inet.h>
#include "classifier.hpp"
#include "dual_queue.hpp"
#include "pi2_aqm.hpp"
#include "scheduler.hpp"
#include "queue_protection.hpp"
using namespace std::chrono;

void make_pkt(uint8_t* f, int len, uint16_t sport, uint8_t ecn) {
    memset(f,0,len); f[12]=8; f[13]=0; uint8_t* ip=f+14; ip[0]=0x45; ip[1]=ecn; ip[8]=64; ip[9]=17;
    uint32_t s=inet_addr("10.0.1.1"), d=inet_addr("10.0.2.1"); memcpy(ip+12,&s,4); memcpy(ip+16,&d,4);
    ip[20]=sport>>8; ip[21]=sport&0xff; ip[22]=0x14; ip[23]=0x53;
}

void run(const std::string& mode, double seconds) {
    bool use_overload = (mode=="dualq"||mode=="dualq_qp"), use_qp=(mode=="dualq_qp");
    AQMConfig ac; PI2AQM aqm(ac); SchedulerConfig sc; sc.classic_max_wait_ms=50; DualQScheduler sched(sc);
    DualQueue q(1500000); QueueProtection qp(30000, 1000);
    double p_cmax = 1.0/(ac.k_factor*ac.k_factor), rate=50e6;
    uint8_t flood[1442], classic[1514], probe[100];
    make_pkt(flood,1442,40000,1); make_pkt(classic,1514,40001,0); make_pkt(probe,100,40002,1);
    struct Src { uint8_t* f; int len; double bps; Clock::time_point next; } src[3];
    auto t0 = Clock::now();
    src[0]={flood,1442,60e6,t0}; src[1]={classic,1514,10e6,t0}; src[2]={probe,100,100*8/0.01,t0};
    auto next_tx=t0, next_up=t0;
    double sent_bytes[3]={0}, probe_delay_sum=0; int probe_n=0; long l_drops=0,c_drops=0,redir=0;
    double l_delay_sum=0, c_delay_sum=0; int samples=0; auto next_sample=t0+milliseconds(1000);
    while (Clock::now()-t0 < duration<double>(seconds)) {
        auto now = Clock::now();
        for (int i=0;i<3;i++) while (now >= src[i].next) {        // arrivals
            PacketInfo info = parse_packet(src[i].f, src[i].len);
            bool to_l4s = info.is_l4s, sanc=false;
            if (to_l4s && use_qp && !qp.allow_l4s(info, src[i].len, now)) { to_l4s=false; sanc=true; redir++; }
            if (q.enqueue(src[i].f, src[i].len, info.ecn, to_l4s, info.flow_id, sanc) && to_l4s && use_qp) qp.add_bytes(info.flow_id, src[i].len);
            src[i].next += duration_cast<Clock::duration>(duration<double>(src[i].len*8.0/src[i].bps));
        }
        if (now >= next_up) {                                       // PI2 update
            double d = q.classic_delay_ms(now);
            if (use_overload && q.l4s_delay_ms(now) > d) d = q.l4s_delay_ms(now);
            aqm.update_control_loop(d); next_up += milliseconds(16);
        }
        if (now >= next_tx && !q.both_empty()) {                    // dequeue
            SelectedQueue s = sched.select_next_queue(q.classic_empty(), q.l4s_empty(), q.classic_head_time());
            Packet p = (s==SelectedQueue::L4S) ? q.pop_l4s() : q.pop_classic();
            double soj = duration<double,std::milli>(now-p.enqueue_time).count();
            bool drop=false, overload = use_overload && aqm.read_p_c() >= p_cmax;
            if (use_qp && s==SelectedQueue::L4S) qp.remove_bytes(p.flow_id, p.data.size());
            if (s==SelectedQueue::CLASSIC) { if (aqm.should_drop_classic(soj) && (p.ecn==ECN_NOT_ECT||overload||p.sanctioned)) { drop=true; c_drops++; } }
            else { if (overload && aqm.should_drop_classic(soj)) { drop=true; l_drops++; } else aqm.should_mark_l4s(soj); }
            if (!drop) {
                int len=p.data.size(); int who = (len==1442)?0:(len==1514)?1:2;
                if (now-t0 > std::chrono::seconds(2)) { sent_bytes[who]+=len; if (who==2){probe_delay_sum+=soj;probe_n++;} }
                sched.notify_packet_dispatched(s,len);
                if (now-next_tx > milliseconds(1)) next_tx=now;
                next_tx += duration_cast<Clock::duration>(duration<double>(len*8.0/rate));
            }
        }
        if (now >= next_sample) { l_delay_sum+=q.l4s_delay_ms(now); c_delay_sum+=q.classic_delay_ms(now); samples++; next_sample+=milliseconds(50); }
    }
    double meas = seconds-2;
    printf("%-12s probe delay %7.2f ms | L queue %7.2f ms | C queue %6.2f ms | tput flood %5.1f classic %5.1f Mbps | p_C %.3f | L drops %ld C drops %ld redirects %ld\n",
        mode.c_str(), probe_n? probe_delay_sum/probe_n:0, l_delay_sum/samples, c_delay_sum/samples,
        sent_bytes[0]*8/meas/1e6, sent_bytes[1]*8/meas/1e6, aqm.read_p_c(), l_drops, c_drops, redir);
}
int main(){ for (auto m : {"dualq_basic","dualq","dualq_qp"}) run(m, 6); }
