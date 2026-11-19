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
├── Makefile                     # C++ compilation flags (-O3, -Wall, pthreads)
├── README.md                    # System documentation and execution guidelines
├── requirements.txt             # Python libraries for evaluation & telemetry
├── .gitignore                   # Excludes build artifacts, logs, and CSV outputs
├── config/
│   └── router_config.ini        # Queue capacities, link limits, target delays
├── include/                     # C++ Header declarations
│   ├── classifier.hpp           # Layer 2/3 header parsers and ECN bit extraction
│   ├── dual_queue.hpp           # Memory buffers and sojourn timestamping structs
│   ├── pi2_aqm.hpp              # RFC 9332 coupled probability math engine
│   ├── raw_socket.hpp           # AF_PACKET promiscuous socket binding utilities
│   └── scheduler.hpp            # Modified Deficit Round Robin dispatcher
├── src/                         # C++ Source implementations
│   ├── main.cpp                 # Forwarder lifecycle, signal handling, and pipeline
│   ├── classifier.cpp           # IP/TCP/UDP header parsing logic
│   ├── dual_queue.cpp           # Packet enqueue, ring buffers, and drop routines
│   ├── pi2_aqm.cpp              # PI controller periodic sampling & coupled marking
│   ├── raw_socket.cpp           # Interface capture and frame transmission
│   └── scheduler.cpp            # Dual-queue egress dispatcher
├── mininet/                     # Emulated network environment
│   ├── topo_bottleneck.py       # Mininet 3-node dual-subnet bottleneck topology
│   └── configure_routes.sh      # Disables OS kernel IP forwarding; binds routes
└── eval/                        # Benchmarking & performance evaluation
    ├── traffic_profile.sh       # Orchestrates iperf3 bulk load and latency probes
    ├── latency_probe.py         # Sub-millisecond UDP latency measurement script
    ├── parse_metrics.py         # Computes P50, P95, P99 RTT percentiles and jitter
    ├── plot_results.py          # Renders latency CDFs and throughput time series
    └── results/                 # Directory holding benchmark CSVs and PNG graphs
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

### Step 4: Run the Automated Evaluation Suite
You can execute tests directly from the Mininet CLI:
```bash
mininet> h2 iperf3 -s -p 5201 &                      # Classic (cubic) receiver
mininet> h2 iperf3 -s -p 5202 &                      # L4S (DCTCP + ECT(1)) receiver
mininet> h2 python3 eval/latency_probe.py --server &
mininet> h1 iperf3 -c 10.0.2.1 -p 5201 -t 40 &       # Classic bulk flow
mininet> h1 iperf3 -c 10.0.2.1 -p 5202 -C dctcp -t 40 &   # L4S bulk flow
mininet> h1 python3 eval/latency_probe.py --dest 10.0.2.1 --ecn 1 --duration 30 --out eval/results/probe_l4s.csv
```
Or run the automated orchestrator from an external terminal:
```bash
sudo ./eval/traffic_profile.sh
```

### Step 5: Generate Metrics & Visualizations
```bash
python3 eval/parse_metrics.py --input eval/results/
python3 eval/plot_results.py --input eval/results/
```
Output graphs are saved to `eval/results/`.

---

## 6. Team Division & Task Ownership

| Member | Primary Ownership | Assigned Source Files | Checkpoint Deliverables |
| :--- | :--- | :--- | :--- |
| **Vasant** | Controller Math & Starvation-Free Scheduler | `include/pi2_aqm.hpp`, `src/pi2_aqm.cpp`, `include/scheduler.hpp`, `src/scheduler.cpp` | System block diagrams, queue state-machine flowcharts, RFC 9332 PI tuning constants |
| **Asit** | Emulation Topology & Evaluation Harness | `mininet/topo_bottleneck.py`, `mininet/configure_routes.sh`, `eval/traffic_profile.sh`, `eval/latency_probe.py` | Mininet dual-subnet setup, synthetic traffic matrix, automated latency CSV logging |
| **Rishit** | Wire Capture Engine & Packet Classifier | `include/raw_socket.hpp`, `src/raw_socket.cpp`, `include/classifier.hpp`, `src/classifier.cpp`, `src/main.cpp` | AF_PACKET raw socket binding, IPv4 ToS/ECN classification logic, checksum recalculation |

---

## 7. 8-Week Project Milestone Timeline

```text
Week 1–2: Checkpoint Sprint (Current)
├── Configure Mininet dual-subnet bottleneck (50 Mbps, 20 ms delay)
├── Verify baseline bufferbloat (DropTail RTT spikes > 150 ms)
├── Implement raw socket ECN classifier proof-of-concept
└── Checkpoint 1 Presentation: Deliver block diagram, flowchart, and working packet parser

Week 3–4: Core Forwarding & Ring Buffer Engine
├── Implement thread-safe dual ring buffers (Classic deep queue vs. L4S shallow queue)
├── Establish userspace Layer-3 packet forwarding across r0-eth0 and r0-eth1
└── Verify wire-speed packet processing without userspace memory exhaustion

Week 5–6: RFC 9332 Coupled PI2 Controller & Scheduler
├── Integrate discrete-time PI controller loop (16 ms sampling period)
├── Implement mathematical coupling: p_C = (p_L / 2)^2
├── Implement dynamic CE marking (ECN bit rewrite to 11 and checksum recalculation)
└── Add starvation protection scheduler to safeguard Classic bandwidth share

Week 7–8: Benchmarking, Validation & Final Evaluation
├── Run automated traffic matrices across varying loads (10 Mbps to 100 Mbps)
├── Benchmark against Linux kernel DropTail and FQ-CoDel baselines
├── Generate P50, P95, and P99 latency CDFs and throughput graphs
└── Final Project Evaluation & Live Demonstration
```

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
* **De Schepper, K., et al.:** *"PI2: A Linearized AQM for both Classic and Scalable TCP,"* ACM CoNEXT, 2016.