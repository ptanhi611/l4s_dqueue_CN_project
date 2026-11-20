# Experiment log (lab notebook)

Every run of `eval/traffic_profile.sh` is archived automatically in
`experiments/<date_time>_<label>/` (all CSV/JSON results, graphs, `console.txt`,
and the `router_config.ini` that was used). After each run, add a short entry
below: what changed, what we saw, and whether the run is valid. Keep failed runs
too. They explain our design decisions in the report.

## Common setup (unless an entry says otherwise)

| Item | Value |
|---|---|
| Topology | h1 (10.0.1.1) — r0 — h2 (10.0.2.1), Mininet, Linux 6.17+ |
| Bottleneck | 50 Mbps on r0 → h2. Our router: paced inside `l4s_router`. Kernel modes: HTB 50 Mbit + qdisc |
| Base RTT | 20 ms (netem delay on h2's egress, ACK path) |
| Buffer | 1.5 MB shared (our router); kernel defaults for fq_codel / dualpi2 |
| Classic flow | iperf3 TCP cubic, h1 → h2:5201, no ECN |
| L4S flow | iperf3 TCP DCTCP, h1 → h2:5202, ECT(0) rewritten to ECT(1) by `tc pedit` on h1 |
| Probes | UDP, 100 B every 10 ms, one ECT(1) (L4S queue), one Not-ECT (Classic queue) |
| AQM settings | target 15 ms, Tupdate 16 ms, α 0.16, β 3.2, k 2, L4S step 1 ms (RFC 9332 defaults) |

---

## Run 1: 2026-11-20 ~05:15, first full comparison

- **Code:** batches 1+2 (first working forwarder + automation)
- **Folder:** `2026-11-20_run1_first_full_comparison/` (console only, raw files were overwritten)
- **Status:** partly valid. FIFO and Classic-side results valid. L4S results invalid (bug).

| Mode | L4S probe p50/p95/p99 (ms) | Classic probe p50/p95/p99 (ms) | Tput C / L / total (Mbps) |
|---|---|---|---|
| fifo | 235.9 / 311.3 / 315.4 | 235.8 / 311.2 / 315.7 | 15.3 / 23.4 / 38.7 |
| dualq | 224.1 / 394.7 / 395.7 | 34.7 / 45.0 / 70.2 | 8.8 / 29.9 / 38.7 |
| fq_codel | 20.2 / 20.3 / 20.4 | 20.2 / 20.4 / 20.4 | 23.5 / 24.0 / 47.5 |
| dualpi2 (kernel) | 35.8 / 44.6 / 46.7 | 20.5 / 24.1 / 45.7 (15.8% loss) | 0.8 / 46.7 / 47.5 |

**Findings**
1. **Bufferbloat reproduced.** FIFO p95 311 ms matches the time to drain a full buffer: 1.5 MB × 8 / 39 Mbps ≈ 310 ms.
2. **PI2 Classic control works.** In dualq, the Classic probe p50 is 34.7 ms = 20 ms base + 15 ms target.
3. **Scheduler works as designed.** Classic got ~23% of the link when both queues were busy, close to the expected ~25% from the 4500 B L4S quantum.
4. **Bug: the L4S flow ignored marks.** ECN was not negotiated, so CE marks had no effect and the L4S queue filled the whole buffer. Check: 1.5 MB / (0.77 × 39 Mbps) ≈ 400 ms, and we measured 395 ms. Cause, found in Run 2: DCTCP sends its SYN as ECT(0), and h2's cubic listener refuses ECN for an ECN-marked SYN.
5. **Accidental "attack" experiment.** With the same unresponsive L4S flow, kernel dualpi2 kept the L4S delay bounded (overload handling) but starved Classic (0.8 Mbps, 15.8% probe loss). Our router (no overload handling yet) filled its buffer. **Neither handles an unresponsive L4S flow well.** This is the motivation for queue protection (Phase 3).
6. **Bug: our router reached only ~39 of ~47.5 Mbps.** Pacing restarted its schedule from "now" after every slightly late wakeup. An isolated timing test showed 35.7 Mbps with the old logic and 49.9 Mbps with the fix.

**Use in report:** bufferbloat baseline numbers; motivation for queue protection (finding 5); debugging and validation story.

---

## Run 2: 2026-11-20 ~05:45, after the pacing fix

- **Code:** batches 1+2+3 (pacing fix, ECN check, queue-delay metric)
- **Folder:** `2026-11-20_run2_pacing_fixed_classic_only/`
- **Status:** valid as a **Classic-traffic validation** run. The L4S bulk flow was not L4S (bug), so all bulk traffic was Classic.

| Mode | L4S probe p50/p99 (ms) | Classic probe p50/p95/p99 (ms) | Classic queue mean/p99 (ms) | Tput C / L / total |
|---|---|---|---|---|
| fifo | 194.7 / 259.9 | 194.5 / 255.9 / 259.9 | 135.7 / 239.8 (single queue) | 18.9 / 28.6 / 47.5 |
| dualq (ours) | 20.2 / 20.3 | **34.1 / 40.4 / 41.7** | **10.6 / 20.9** | **24.8 / 22.7 / 47.5** |
| fq_codel | 20.2 / 20.5 | 20.2 / 20.3 / 20.4 | n/a | 24.4 / 22.3 / 46.8 |
| dualpi2 (kernel) | 20.2 / 20.4 | **34.2 / 40.0 / 42.7** | **11.9 / 22.0** | **24.9 / 22.6 / 47.5** |

**Findings**
1. **Pacing fix confirmed.** Our router now carries 47.5 Mbps, the same as the kernel modes. Run 1 gave 38.7 Mbps.
2. **Bufferbloat:** FIFO p99 260 ms ≈ full buffer at 47.5 Mbps (1.5 MB × 8 / 47.5 Mbps ≈ 253 ms).
3. **ECN still not negotiated.** `sysctl net.ipv4.tcp_congestion_control=dctcp` failed inside the Mininet host with "Operation not permitted". Linux only allows a non-default congestion control inside a namespace if it is on the *allowed* list, which can only be changed from the main namespace. So DCTCP fell back to `dctcp-reno`, sent Not-ECT, and the "L4S" flow went to the Classic queue. Evidence: kernel dualpi2 `pkts_in_l 3000` = exactly the number of L4S probe packets; our router's L4S queue delay was 0.
4. **Our Classic path matches Linux `sch_dualpi2`.** With only Classic bulk traffic, our dualq and the kernel's dualpi2 give almost the same numbers: Classic probe p50 34.1 vs 34.2 ms, p99 41.7 vs 42.7 ms, Classic queue mean 10.6 vs 11.9 ms, throughput split 24.8/22.7 vs 24.9/22.6 Mbps.
5. **Sparse L4S traffic sees no queuing** (probe = 20.2 ms = base RTT) in both dual-queue implementations, like fq_codel.

**Use in report:** "Validation against Linux sch_dualpi2 (Classic traffic)" table (finding 4); pacing before/after (finding 1).

---

## Sim 1: 2026-11-20, offline attack simulation (no Mininet)

- **Code:** batch 4 classes (DualQueue, PI2AQM, DualQScheduler, QueueProtection) with the same decision logic as `main.cpp`
- **Folder:** `2026-11-20_sim1_offline_attack_logic/` (`sim_attack.cpp` + `output.txt`, build command inside)
- **Traffic:** unresponsive 60 Mbps ECT(1) UDP flood + unresponsive 10 Mbps Not-ECT Classic stream + 100 B ECT(1) probe every 10 ms, 50 Mbps link, 6 s per mode (first 2 s skipped). No TCP, so no flow reacts to marks or drops.
- **Status:** valid as a logic check of overload handling and queue protection. Not a replacement for the Mininet runs.

| Mode | L4S probe queue delay | L4S queue | Classic queue | Flood / Classic tput (Mbps) | p_C |
|---|---|---|---|---|---|
| dualq_basic | **259 ms** | 259 ms | 0.0 ms | 46.1 / 3.6 | 0.00 |
| dualq (overload) | **15.5 ms** | 14.8 ms | 0.0 ms | 42.8 / 7.1 | 0.30 |
| dualq_qp | **0.24 ms** | 0.21 ms | 16.0 ms | 42.6 / 7.2 | 0.28 |

**Findings**
1. **Without overload handling**, an unresponsive ECT(1) flow fills the whole shared buffer (259 ms) and Classic packets get tail-dropped (3.6 of 10 Mbps).
2. **RFC overload handling bounds the L4S delay at about the 15 ms target**, the same behaviour as kernel dualpi2 in Run 1 (~15 ms queue). But low latency for L4S is lost: L4S is now as slow as Classic.
3. **Queue protection restores L4S latency (0.24 ms)** by moving the flood to the Classic queue, where the AQM holds it at the 15 ms target.
4. **Honest limitation:** the flood still competes with Classic traffic inside the Classic queue (Classic stream 7.2 of 10 Mbps). Queue protection protects the L4S queue; it does not give per-flow fairness. Only fq_codel isolates every flow. Mention this in the report as a trade-off: per-flow state for the whole link vs a small per-flow check only on the L4S queue.

---

## Run 3: (next) with real L4S traffic, Phase 2 + Phase 3

- **Code:** batch 4 (DCTCP allowed-list fix, RFC overload handling, queue protection, attack scenario)
- Planned: `normal` scenario (all modes), then `attack` scenario (ECT(1) UDP flood, 60 Mbps, t = 10–30 s of 40 s)
- **What to check first:** every mode prints `OK: L4S flow is using DCTCP with ECN`

---

## Entry template

```
## Run N: <date time>, <label>
- Code: <git commit / what changed>
- Folder: <experiments/...>
- Scenario / modes / duration:
- Status: valid / partly valid / invalid (why)
- Results: <table or key numbers>
- Findings: <numbered, with a number to back each one>
- Use in report: <which section / graph>
```
