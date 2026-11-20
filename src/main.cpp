// L4S Dual-Queue software router (runs on Mininet node r0)
//
//   h1 ---> r0-eth0 [ classify -> DualQueue -> PI2 AQM + scheduler -> 50 Mbps pacing ] r0-eth1 ---> h2
//   h1 <--- r0-eth0 <------------------- forwarded straight away (ACKs) ------------- r0-eth1 <--- h2
//
// Usage:  sudo ./build/l4s_router config/router_config.ini [fifo|dualq] [stats.csv]

#include <arpa/inet.h>
#include <poll.h>
#include <signal.h>
#include <sys/prctl.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <string>

#include "classifier.hpp"
#include "dual_queue.hpp"
#include "pi2_aqm.hpp"
#include "raw_socket.hpp"
#include "scheduler.hpp"

static volatile sig_atomic_t running = 1;

void handle_signal(int) {
    running = 0;
}

// ---------------- small config file reader ----------------
// Reads "key = value" lines. Section names like [aqm] are only for humans.

std::string trim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t\r\n");
    size_t end = s.find_last_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    return s.substr(start, end - start + 1);
}

std::map<std::string, std::string> load_config(const std::string& path) {
    std::map<std::string, std::string> cfg;
    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "Could not open config file " << path << ", using defaults\n";
        return cfg;
    }
    std::string line;
    while (std::getline(file, line)) {
        size_t comment = line.find('#');
        if (comment != std::string::npos) line = line.substr(0, comment);

        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;  // empty line or [section]

        cfg[trim(line.substr(0, eq))] = trim(line.substr(eq + 1));
    }
    return cfg;
}

double get_num(std::map<std::string, std::string>& cfg, const std::string& key, double def) {
    if (cfg.count(key)) return std::stod(cfg[key]);
    return def;
}

std::string get_str(std::map<std::string, std::string>& cfg, const std::string& key, const std::string& def) {
    if (cfg.count(key)) return cfg[key];
    return def;
}

bool parse_mac(const std::string& s, uint8_t* mac) {
    int n = sscanf(s.c_str(), "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx",
                   &mac[0], &mac[1], &mac[2], &mac[3], &mac[4], &mac[5]);
    return n == 6;
}

double ms_between(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}

// ---------------- counters for the summary and the stats file ----------------
struct Stats {
    uint64_t rx_pkts = 0;      // packets from h1 side that we queued
    uint64_t sent_pkts = 0;    // packets sent to h2
    uint64_t sent_bytes = 0;
    uint64_t tail_drops = 0;   // buffer full
    uint64_t aqm_drops = 0;    // PI2 dropped a Not-ECT classic packet
    uint64_t c_marks = 0;      // PI2 CE-marked an ECT(0) classic packet
    uint64_t l_marks = 0;      // CE marks in the L4S queue
    uint64_t rev_pkts = 0;     // packets h2 -> h1 (ACKs)
    uint64_t send_errors = 0;
    uint64_t big_frames = 0;   // frames > 1514 bytes, means offloads are still ON
};

int main(int argc, char* argv[]) {

    if (argc < 2) {
        printf("Usage: %s <config.ini> [fifo|dualq] [stats.csv]\n", argv[0]);
        return 1;
    }

    std::map<std::string, std::string> cfg = load_config(argv[1]);

    // ---------- read settings ----------
    std::string mode = get_str(cfg, "mode", "dualq");
    if (argc >= 3) mode = argv[2];  // command line overrides config
    if (mode != "fifo" && mode != "dualq") {
        printf("mode must be fifo or dualq\n");
        return 1;
    }
    bool use_aqm = (mode == "dualq");

    std::string in_if = get_str(cfg, "in_if", "r0-eth0");
    std::string out_if = get_str(cfg, "out_if", "r0-eth1");
    uint32_t h1_ip = inet_addr(get_str(cfg, "h1_ip", "10.0.1.1").c_str());
    uint32_t h2_ip = inet_addr(get_str(cfg, "h2_ip", "10.0.2.1").c_str());

    uint8_t h1_mac[6], h2_mac[6];
    if (!parse_mac(get_str(cfg, "h1_mac", "00:00:00:00:01:01"), h1_mac) ||
        !parse_mac(get_str(cfg, "h2_mac", "00:00:00:00:02:01"), h2_mac)) {
        printf("Bad MAC address in config\n");
        return 1;
    }

    double rate_bps = get_num(cfg, "rate_mbps", 50.0) * 1e6;
    uint32_t limit_bytes = (uint32_t)get_num(cfg, "queue_limit_bytes", 1500000);
    std::string stats_path = get_str(cfg, "stats_file", "eval/results/router_stats.csv");
    if (argc >= 4) stats_path = argv[3];  // command line overrides config
    double stats_interval_ms = get_num(cfg, "stats_interval_ms", 100.0);

    AQMConfig aqm_cfg;
    aqm_cfg.target_c_ms = get_num(cfg, "target_c_ms", aqm_cfg.target_c_ms);
    aqm_cfg.target_l_ms = get_num(cfg, "target_l_ms", aqm_cfg.target_l_ms);
    aqm_cfg.time_update_ms = get_num(cfg, "time_update_ms", aqm_cfg.time_update_ms);
    aqm_cfg.alpha = get_num(cfg, "alpha", aqm_cfg.alpha);
    aqm_cfg.beta = get_num(cfg, "beta", aqm_cfg.beta);
    aqm_cfg.k_factor = get_num(cfg, "k_factor", aqm_cfg.k_factor);

    SchedulerConfig sched_cfg;
    sched_cfg.l_quantum_bytes = (uint32_t)get_num(cfg, "l_quantum_bytes", sched_cfg.l_quantum_bytes);
    sched_cfg.classic_max_wait_ms = get_num(cfg, "classic_max_wait_ms", sched_cfg.classic_max_wait_ms);

    printf("Mode: %s | rate %.1f Mbps | buffer %u bytes\n", mode.c_str(), rate_bps / 1e6, limit_bytes);

    // ---------- open sockets ----------
    RawSocket sock_in, sock_out;
    if (!sock_in.open_socket(in_if)) return 1;
    if (!sock_out.open_socket(out_if)) return 1;

    DualQueue queue(limit_bytes);
    PI2AQM aqm(aqm_cfg);
    DualQScheduler scheduler(sched_cfg);
    Stats st;

    FILE* stats_file = fopen(stats_path.c_str(), "w");
    if (stats_file) {
        fprintf(stats_file, "time_s,c_pkts,l_pkts,c_delay_ms,l_delay_ms,p_prime,p_c,p_l,"
                            "sent_pkts,sent_bytes,tail_drops,aqm_drops,c_marks,l_marks\n");
    } else {
        printf("Warning: could not open %s, no stats will be written\n", stats_path.c_str());
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    // Ask Linux to wake us up on time (default timer slack is 50 us,
    // which is a big error when one packet takes only ~240 us at 50 Mbps)
    prctl(PR_SET_TIMERSLACK, 1);

    uint8_t buf[65536];
    Clock::time_point start = Clock::now();
    Clock::time_point next_tx = start;      // when the 50 Mbps link is free again
    Clock::time_point next_update = start;  // next PI2 update
    Clock::time_point next_stats = start;
    auto update_step = std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double, std::milli>(aqm_cfg.time_update_ms));
    auto stats_step = std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double, std::milli>(stats_interval_ms));

    printf("Router running, press Ctrl+C to stop\n");

    while (running) {
        int n;

        // ---------- 1. h1 -> h2 direction: classify and enqueue ----------
        while ((n = sock_in.receive_frame(buf, sizeof(buf))) > 0) {
            if (n > 1514) st.big_frames++;

            PacketInfo info = parse_packet(buf, n);
            if (!info.is_ipv4 || info.dst_ip != h2_ip) continue;  // ARP, IPv6, or traffic for r0 itself
            if (!decrement_ttl(buf)) continue;
            set_macs(buf, sock_out.get_mac(), h2_mac);

            bool to_l4s = use_aqm && info.is_l4s;  // in fifo mode everything uses one queue
            if (queue.enqueue(buf, n, info.ecn, to_l4s)) st.rx_pkts++;
            else st.tail_drops++;
        }

        // ---------- 2. h2 -> h1 direction: forward straight away ----------
        while ((n = sock_out.receive_frame(buf, sizeof(buf))) > 0) {
            PacketInfo info = parse_packet(buf, n);
            if (!info.is_ipv4 || info.dst_ip != h1_ip) continue;
            if (!decrement_ttl(buf)) continue;
            set_macs(buf, sock_in.get_mac(), h1_mac);
            if (sock_in.send_frame(buf, n)) st.rev_pkts++;
            else st.send_errors++;
        }

        Clock::time_point now = Clock::now();

        // ---------- 3. PI2 controller update every Tupdate ----------
        if (now >= next_update) {
            if (use_aqm) aqm.update_control_loop(queue.classic_delay_ms(now));
            next_update += update_step;
        }

        // ---------- 4. send one packet if the bottleneck link is free ----------
        if (now >= next_tx && !queue.both_empty()) {

            SelectedQueue q;
            if (use_aqm) q = scheduler.select_next_queue(queue.classic_empty(), queue.l4s_empty(), queue.classic_head_time());
            else q = SelectedQueue::CLASSIC;

            Packet p = (q == SelectedQueue::L4S) ? queue.pop_l4s() : queue.pop_classic();
            double sojourn_ms = ms_between(p.enqueue_time, now);
            bool drop = false;

            if (use_aqm && q == SelectedQueue::CLASSIC) {
                if (aqm.should_drop_classic(sojourn_ms)) {
                    // RFC 9332: drop Not-ECT packets, CE-mark ECN-capable ones
                    if (p.ecn == ECN_NOT_ECT) {
                        drop = true;
                        st.aqm_drops++;
                    } else {
                        set_ce_mark(p.data.data());
                        st.c_marks++;
                    }
                }
            } else if (use_aqm && q == SelectedQueue::L4S) {
                if (aqm.should_mark_l4s(sojourn_ms)) {
                    set_ce_mark(p.data.data());
                    st.l_marks++;
                }
            }

            if (!drop) {
                int len = p.data.size();
                if (sock_out.send_frame(p.data.data(), len)) {
                    st.sent_pkts++;
                    st.sent_bytes += len;
                } else {
                    st.send_errors++;
                }
                if (use_aqm) scheduler.notify_packet_dispatched(q, len);

                // Pacing: this packet keeps the link busy for len*8/rate seconds.
                // Keep to the schedule: if we woke up a little late, the next packet
                // goes out right away to catch up. Only when the link was idle
                // (more than 1 ms behind) do we restart the schedule from now.
                if (now - next_tx > std::chrono::milliseconds(1)) next_tx = now;
                next_tx += std::chrono::duration_cast<Clock::duration>(
                    std::chrono::duration<double>(len * 8.0 / rate_bps));
            }
            // If dropped, the link is still free, so the next loop sends the next packet
        }

        // ---------- 5. write one stats line every stats_interval_ms ----------
        if (now >= next_stats) {
            if (stats_file) {
                fprintf(stats_file, "%.3f,%zu,%zu,%.3f,%.3f,%.5f,%.5f,%.5f,%lu,%lu,%lu,%lu,%lu,%lu\n",
                        ms_between(start, now) / 1000.0,
                        queue.classic_packets(), queue.l4s_packets(),
                        queue.classic_delay_ms(now), queue.l4s_delay_ms(now),
                        aqm.read_p_prime(), aqm.read_p_c(), aqm.read_p_cl(),
                        (unsigned long)st.sent_pkts, (unsigned long)st.sent_bytes,
                        (unsigned long)st.tail_drops, (unsigned long)st.aqm_drops,
                        (unsigned long)st.c_marks, (unsigned long)st.l_marks);
                fflush(stats_file);
            }
            next_stats += stats_step;
        }

        // ---------- 6. sleep until something needs doing ----------
        // Wake up for: a new packet, the link becoming free, a PI2 update, or a stats line.
        Clock::time_point wake = (next_update < next_stats) ? next_update : next_stats;
        if (!queue.both_empty() && next_tx < wake) wake = next_tx;

        now = Clock::now();
        if (wake > now) {
            auto wait_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(wake - now).count();
            struct timespec ts;
            ts.tv_sec = wait_ns / 1000000000;
            ts.tv_nsec = wait_ns % 1000000000;

            struct pollfd fds[2];
            fds[0].fd = sock_in.get_fd();
            fds[0].events = POLLIN;
            fds[1].fd = sock_out.get_fd();
            fds[1].events = POLLIN;
            ppoll(fds, 2, &ts, nullptr);
        }
    }

    // ---------- summary ----------
    printf("\n----- %s summary -----\n", mode.c_str());
    printf("queued %lu, sent %lu (%.1f MB), reverse %lu\n",
           (unsigned long)st.rx_pkts, (unsigned long)st.sent_pkts, st.sent_bytes / 1e6, (unsigned long)st.rev_pkts);
    printf("tail drops %lu, AQM drops %lu, classic CE marks %lu, L4S CE marks %lu\n",
           (unsigned long)st.tail_drops, (unsigned long)st.aqm_drops,
           (unsigned long)st.c_marks, (unsigned long)st.l_marks);
    if (st.send_errors) printf("send errors: %lu\n", (unsigned long)st.send_errors);
    unsigned int kernel_drops = sock_in.get_kernel_drops();
    if (kernel_drops) printf("WARNING: kernel dropped %u frames before we could read them (router too slow?)\n", kernel_drops);
    if (st.big_frames) printf("WARNING: %lu frames > 1514 bytes. Turn off offloads (see mininet/configure_routes.sh)\n",
                              (unsigned long)st.big_frames);

    if (stats_file) fclose(stats_file);
    sock_in.close_socket();
    sock_out.close_socket();
    return 0;
}
