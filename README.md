# Mitigating Bufferbloat via Emulated L4S Dual-Queue Coupled AQM

An emulated software-defined edge forwarder implementing IETF RFC 9331 and RFC 9332 (DualQ Coupled Active Queue Management) to eliminate bufferbloat and guarantee sub-5 ms latency for interactive flows under link saturation.

---

## 1. Project Overview & Motivation

Standard network routers combine interactive, latency-sensitive traffic (voice, video calls, gaming) and heavy bulk data (file downloads, system updates) into single FIFO queues. When bulk TCP streams saturate link capacity, router queues fill completely ("bufferbloat"), inflating round-trip latency from tens of milliseconds to over 300 ms.

This project implements a userspace Layer-3 software router running **Dual-Queue Coupled AQM**:
* **Wire Classification (RFC 9331):** Uses raw network sockets (`AF_PACKET`) to inspect the IPv4 Type of Service (ToS) / ECN field. It routes classic packets (`Not-ECT`, `ECT(0)`) to a deep Classic Queue and low-latency packets (`ECT(1)`, `CE`) to a shallow L4S Queue.
* **Coupled PI2 AQM (RFC 9332):** Dynamically couples the Classic drop probability ($p_C$) with the L4S marking probability ($p_L$) using the square-law relationship $p_C = (p_L / k)^2$. This signals senders to throttle throughput gracefully without dropping interactive packets or inducing queue delay.
* **Starvation-Free Scheduler:** Drains packets using modified deficit scheduling, guaranteeing high-priority service for L4S packets while preserving a dedicated bandwidth share for Classic flows.

---

## 2. System Architecture & Topology

```text
                  Mininet Emulated Topology (Linux Network Namespaces)

  [ Host 1: Sender ]                                                [ Host 2: Receiver ]
    (10.0.1.1/24)                                                     (10.0.2.1/24)
          │                                                                 ▲
   1 Gbps │ (veth)                                                   (veth) │ 1 Gbps
          ▼                                                                 │
     [ r0-eth0 ]                                                       [ r0-eth1 ]
          │                                                                 ▲
          └─────────────► [ Custom L4S Software Forwarder ] ────────────────┘
                            (src/main.cpp on Node r0)
                            Bottleneck: 50 Mbps, 20 ms delay
                            
               ┌───────────────────────┴───────────────────────┐
               ▼ (ECN == 00, 10)                               ▼ (ECN == 01, 11)
      ┌─────────────────┐                             ┌─────────────────┐
      │  Classic Queue  │                             │    L4S Queue    │
      │  (Deep Buffer)  │                             │ (Shallow Buffer)│
      └────────┬────────┘                             └────────┬────────┘
               │                                               │
               │               Coupled Feedback                │
               │ ◄───────────────────────────────────────────► │
               │               p_C = (p_L / 2)^2               │
               │                                               │
               │  Drop Engine                   Mark Engine    │
               │  (Random Drop)                 (Mark CE: 11)  │
               └───────────────┬───────────────┬───────────────┘
                               │               │
                               ▼               ▼
                           [ Priority / DRR Scheduler ]
                                       │
                                       ▼
                             Egress via Raw Socket
```

---

## 3. Repository Structure

The repository is organized into distinct modules across development, configuration, network emulation, and automated benchmarking:

```text
l4s-dualq-router/
├── Makefile                     # builds build/l4s_router (g++ -O2, C++17)
├── README.md
├── config/
│   ├── router_config.ini        # rate, buffer, PI2 (RFC 9332 defaults), scheduler, queue protection
│   └── router_config_breaker_off.ini  # same, starvation breaker off (used by the final suite)
├── include/ , src/              # C++ router (one .hpp/.cpp pair per module)
│   ├── raw_socket               # AF_PACKET sockets on r0-eth0 / r0-eth1
│   ├── classifier               # IPv4 parsing, ECN, flow id, CE marking, TTL + checksum
│   ├── dual_queue               # Classic + L4S queues, shared byte limit, sojourn times
│   ├── pi2_aqm                  # coupled PI2: p', p_C = p'^2, p_CL = k*p', L4S step marking
│   ├── scheduler                # L4S priority with byte quantum + starvation breaker
│   ├── queue_protection         # our addition: per-flow L4S backlog check, sanction, rate limit
│   └── main.cpp                 # modes, 50 Mbps pacing, overload handling, stats CSV
├── mininet/
│   ├── topo_bottleneck.py       # h1 - r0 - h2 topology, automatic experiments, --debug-ecn
│   └── configure_routes.sh      # offloads off, forwarding off, 20 ms delay, DCTCP route, ECT(1) rule
├── eval/
│   ├── traffic_profile.sh       # one experiment set (several modes) + archive to experiments/
│   ├── run_final.sh             # FINAL suite: normal x3, attack x3, breaker off -> results/final/
│   ├── make_final.py            # mean +- std tables and figures for the report
│   ├── latency_probe.py         # UDP RTT probe with chosen ECN codepoint
│   ├── parse_metrics.py         # summary tables of one run
│   ├── plot_results.py          # graphs of one run
│   └── live_view.py             # live terminal dashboard for demos
├── experiments/                 # every run ever made (raw data, graphs, console) + LOG.md notebook
└── results/final/               # final tables and figures used in the report
```

---

## 4. Environment & Dependencies Setup (Ubuntu)

Run all steps on an **Ubuntu Linux** environment.

### Step 1: Install System Packages
```bash
sudo apt update
sudo apt install -y mininet iperf3 g++ make ethtool xterm python3-pip python3-dev
```
> **Important:** If an installation prompt asks: *"Should iperf3 be run as a daemon?"*, select **No**. Running `iperf3` as a daemon locks port 5201 on the host OS, which interferes with Mininet instances.

### Step 2: Configure Display Permissions for Host Terminals
Enable local graphical permissions so Mininet can launch host-specific terminal windows:
```bash
xhost +local:
```

### Step 3: Install Python Evaluation Libraries
```bash
pip3 install -r requirements.txt
```

### Step 4: Verify Mininet Installation
```bash
sudo mn --test pingall
sudo mn -c
```
*(Always execute `sudo mn -c` after running experiments to clean up virtual bridges and network namespaces).*

---

## 5. Build & Execution Workflow

### Step 1: Compile the Software Forwarder
From the repository root:
```bash
make clean
make
```
This produces the compiled binary at `build/l4s_router`.

### Step 2: Launch the Bottleneck Topology
Launch Mininet with custom routing and disabled kernel IP forwarding:
```bash
sudo -E python3 mininet/topo_bottleneck.py
```

### Step 3: Start the Forwarder on Node r0
Inside the Mininet interactive CLI (`mininet>`):
```bash
# mode = dualq (our L4S router) or fifo (single queue baseline)
mininet> r0 ./build/l4s_router config/router_config.ini dualq > eval/results/router_log.txt 2>&1 &
mininet> h1 ping -c 5 10.0.2.1          # sanity check: should work, RTT ~20 ms
```
Stop the router with `mininet> r0 pkill -INT l4s_router` (it prints a summary into `router_log.txt`).
Per-100 ms queue/AQM stats are written to `eval/results/router_stats.csv`.

### Step 4: Run the Automated Experiments (recommended)
One command runs the same experiment in several modes, prints the summary tables,
draws the graphs, and archives everything in `experiments/<date_time>_<label>/`:
```bash
sudo ./eval/traffic_profile.sh <label> [normal|attack] [duration_s] ["mode list"]

sudo ./eval/traffic_profile.sh normal_check                    # fifo dualq dualq_qp fq_codel dualpi2
sudo ./eval/traffic_profile.sh attack_check attack 40          # dualq_basic dualq dualq_qp dualq_qp_police fq_codel dualpi2
```
| Mode | What it is |
|---|---|
| `fifo` | our router, one plain queue (bufferbloat baseline) |
| `dualq_basic` | our router, DualQ + coupled PI2, no overload handling |
| `dualq` | our router, DualQ + coupled PI2 + RFC 9332 overload handling |
| `dualq_qp` | `dualq` + our queue protection for the L4S queue (misbehaving flows moved to Classic) |
| `dualq_qp_police` | `dualq_qp` + rate limit for sanctioned flows (`qp_police_rate_mbps`) |
| `fq_codel` | Linux kernel fq_codel (baseline) |
| `dualpi2` | Linux kernel `sch_dualpi2` (reference RFC 9332 implementation, kernel 6.17+) |

Scenarios: `normal` = 1 Classic (cubic) flow to 10.0.2.1 + 1 L4S (DCTCP, ECT(1)) flow to 10.0.2.2 + 2 latency probes.
(The L4S flow uses h2's second address because h1's route to 10.0.2.2 has `congctl dctcp`: DCTCP must be
active before the SYN to negotiate ECN. Check ECN with `sudo python3 mininet/topo_bottleneck.py --debug-ecn`.)
`attack` = normal + a 60 Mbps ECT(1) UDP flood that ignores CE marks (from t=10 s until 10 s before the end).

### Step 5: Results, Graphs and the Experiment Log
Each run's folder in `experiments/` contains the console output, all raw CSV/JSON files,
`summary.csv`, and the graphs (`latency_cdf.png`, `latency_timeseries.png`, `throughput.png`,
`internals_<mode>.png`). Graphs can be re-drawn from any archived run:
```bash
python3 eval/plot_results.py --input experiments/<run_folder>
python3 eval/parse_metrics.py --input experiments/<run_folder>
```
**After every run, add an entry to `experiments/LOG.md`** (what changed, what we saw, valid or not).

### Step 6: Final Results for the Report (one command)
```bash
make
sudo python3 mininet/topo_bottleneck.py --debug-ecn    # 1 min sanity check: test B must say "DCTCP with ECN is working"
sudo ./eval/run_final.sh                               # ~35 min: normal x3, attack x3, breaker-off run
```
Everything for the report then lives in **`results/final/`** (see its `README.md`):
`table_normal.md`, `table_attack.md`, `table_breaker.md` (mean ± std over the repeats),
`fig_normal.png`, `fig_attack.png`, `fig_bulk_rtt.png`, and copies of the repeat-1 time-series graphs.
The tables can be rebuilt at any time with `python3 eval/make_final.py`.

### Step 7: Live Demo
Two terminals:
```bash
# Terminal 1: run the attack scenario with our final router
sudo ./eval/traffic_profile.sh demo attack 40 "dualq_qp_police"
# Terminal 2: live dashboard (queue delays, p', marks, drops, throughput)
python3 eval/live_view.py eval/results/dualq_qp_police/router_stats.csv
```
Good demo order: `fifo` (bufferbloat) → `dualq` (L4S ~1 ms queue) → attack with `dualq`
(L4S delay goes up to ~15 ms) → attack with `dualq_qp_police` (L4S delay stays low).

---

## 6. Team Division & Task Ownership

| Member | Primary Ownership | Assigned Source Files | Checkpoint Deliverables |
| :--- | :--- | :--- | :--- |
| **Vasant** | Controller Math & Starvation-Free Scheduler | `include/pi2_aqm.hpp`, `src/pi2_aqm.cpp`, `include/scheduler.hpp`, `src/scheduler.cpp` | System block diagrams, queue state-machine flowcharts, RFC 9332 PI tuning constants |
| **Asit** | Emulation Topology & Evaluation Harness | `mininet/topo_bottleneck.py`, `mininet/configure_routes.sh`, `eval/traffic_profile.sh`, `eval/latency_probe.py` | Mininet dual-subnet setup, synthetic traffic matrix, automated latency CSV logging |
| **Rishit** | Wire Capture Engine & Packet Classifier | `include/raw_socket.hpp`, `src/raw_socket.cpp`, `include/classifier.hpp`, `src/classifier.cpp`, `src/main.cpp` | AF_PACKET raw socket binding, IPv4 ToS/ECN classification logic, checksum recalculation |

---

## 7. Project Status

| Milestone | Status |
|---|---|
| Mininet bottleneck topology, bufferbloat baseline (FIFO p99 ≈ 260 ms) | Done |
| Raw-socket forwarder, ECN classifier, CE marking + checksum, 50 Mbps pacing | Done |
| Coupled PI2 AQM + scheduler (RFC 9332), overload handling | Done; matches Linux `sch_dualpi2` |
| Real L4S traffic (DCTCP with ECN, ECT(1)) | Done (per-route DCTCP, see `experiments/LOG.md` Debug 1–2) |
| Baselines: our FIFO, kernel fq_codel, kernel dualpi2 | Done |
| Our contribution: L4S queue protection (+ rate limit for sanctioned flows) | Done; evaluated under an ECT(1) flood |
| Final suite with repeats, tables and figures (`results/final/`) | `sudo ./eval/run_final.sh` |
| Report and live demo | In progress |

The full history of every experiment, including failed runs and how each bug was found, is in
`experiments/LOG.md`.

---

## 8. Git Collaboration Protocol

To maintain a clean Git history and avoid branch conflicts:
1. **Never commit directly to `main` for active module development.**
2. **Branch Naming Standard:**
   * `feat/raw-classifier` (Rishit)
   * `feat/pi2-coupling` (Vasant)
   * `feat/mininet-harness` (Asit)
3. **Daily Sync Command:**
   ```bash
   git checkout main
   git pull origin main
   git checkout feat/<your-branch>
   git merge main
   ```
4. **Interface Cleanup:** Always run `sudo mn -c` before changing branches or pushing code to ensure lingering virtual interfaces are unmounted.

---

## 9. References & Standards

* **RFC 9330:** *Low Latency, Low Loss, and Scalable Throughput (L4S) Internet Architecture* (IETF, 2023).
* **RFC 9331:** *The Explicit Congestion Notification (ECN) Protocol for L4S* (IETF, 2023).
* **RFC 9332:** *Dual-Queue Coupled Active Queue Management (AQM) for Low Latency, Low Loss, and Scalable Throughput (L4S)* (IETF, 2023).
* **De Schepper, K., et al.:** *"PI2: A Linearized AQM for both Classic and Scalable TCP,"* ACM CoNEXT, 2016.* **RFC 8257:** *Data Center TCP (DCTCP): TCP Congestion Control for Data Centers* (IETF, 2017). Used as our scalable (L4S) sender.
* **RFC 8290:** *The Flow Queue CoDel Packet Scheduler and Active Queue Management Algorithm* (IETF, 2018). The `fq_codel` baseline.
* **Linux `sch_dualpi2`** (net/sched/sch_dualpi2.c, mainline since Linux 6.17). Reference DualPI2 implementation, used as our benchmark.
* **L4STeam, TCP Prague and L4S kernel patches:** https://github.com/L4STeam/linux
* **Briscoe, B. and White, G.:** *The DOCSIS Queue Protection Algorithm to Preserve Low Latency* (IETF Internet-Draft, draft-briscoe-docsis-q-protection). Inspiration for our L4S queue protection.
