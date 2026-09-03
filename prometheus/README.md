# P/D Observability

This directory contains the first Prometheus/Grafana baseline for the custom P/D Cell.

## Scrape topology

~~~text
Prometheus
├─ nvidia-dcgm-metrics
│   └─ kube-system / dcgm-exporter / :9200
│
├─ kubernetes-vllm-instances
│   └─ inference / standalone vLLM / :8000
│
├─ kubernetes-vllm-pd-engines
│   ├─ Prefill pf-*-http / :8101+
│   └─ Decode  dc-*-http / :8201+
│
├─ kubernetes-vllm-pd-router
│   └─ pd-router / router-metrics / :29000
│
└─ kubernetes-litellm-proxy-instances
    └─ inference / LiteLLM / :4000
~~~

## Why the P/D jobs are separate

The original discovery rule classified every inference container exposing port
8000 as vLLM.

A P/D Cell introduces a collision:

~~~text
pd-router API       :8000
pd-router metrics   :29000
Prefill metrics     :8101+
Decode metrics      :8201+
~~~

Therefore the standalone vLLM job still keeps port 8000, but explicitly drops
the `pd-router` container. P/D engine and router metrics are discovered by
chart-generated container/port names instead of hard-coded engine port numbers.

## Namespace scope

Inference-related jobs discover both operational namespaces:

~~~text
inference
test
~~~

The Kubernetes namespace is preserved as the Prometheus label:

~~~text
namespace="inference"
namespace="test"
~~~

This is preferred over separate boolean labels such as `inference=true` or
`test=true`: one stable label key gives simpler PromQL, dashboard variables,
recording rules and alert grouping.

Examples:

~~~promql
up{job="kubernetes-vllm-pd-engines", namespace="inference"}

vllm:num_requests_running{
  job="kubernetes-vllm-pd-engines",
  namespace="test",
  pd_role="decode"
}
~~~

The Grafana P/D overview dashboard exposes this as the `$namespace` variable.

## Labels added by relabeling

P/D engine targets expose:

- `namespace`
- `pod`
- `container`
- `model`
- `pd_role=prefill|decode`
- `pd_engine=prefill-N|decode-N`
- `instance=<pod-ip>`

The role labels are intentionally scrape-time labels. No application-side metric
changes are required.

## Router metrics

The cell-local vLLM Router exposes Prometheus on port 29000 by default when
started with:

~~~text
--prometheus-host 0.0.0.0
--prometheus-port 29000
~~~

The P/D chart names that port `router-metrics`, so Prometheus discovery does
not depend on the numeric port.

## Dashboard

`grafana/dashboards/pd-cell-overview.json` is a first operational dashboard,
not the final Qwen3.8 performance dashboard.

It focuses on:

- scrape health
- Prefill/Decode running and waiting requests
- KV-cache pressure
- prompt/generation token rates
- p95 TTFT
- p95 TPOT/ITL
- DCGM GPU utilization / framebuffer use
- vLLM Router scrape health

Before model-specific tuning, validate the actual metric names exposed by the
pinned vLLM image. The dashboard contains a compatibility expression for TPOT
that accepts either `inter_token_latency_seconds` or the older
`time_per_output_token_seconds` histogram.

## Deployment order

1. Merge/deploy the P/D Cell chart changes.
2. Apply the Prometheus values.
3. Confirm all scrape targets are UP.
4. Import/provision the Grafana dashboard.
5. Run a small P/D request and verify Prefill/Decode series separation.
6. Only then begin Qwen3.8 P/D parameter sweeps.


## Short-range dashboard contract

The dashboards are designed to remain meaningful when the Grafana time range is
reduced to windows such as **Last 5 minutes**.

Rules used throughout the dashboards:

~~~text
rate / histogram rate  -> $__rate_interval
selected-run totals    -> increase(...[$__range])
selected-run averages  -> avg_over_time(...[$__range])
no fixed 1h / 24h PromQL windows
~~~

This avoids a common failure mode where a panel is hard-coded to a long range
and shows `No data` for short benchmark runs.

A percentile panel can still legitimately have no value if there were no
requests in the selected range. Target/gauge panels remain available and the
"Requests in Selected Range" panel makes that distinction explicit.

## Extended DCGM collector

The repository includes:

~~~text
prometheus/dcgm-extended-collectors.csv
~~~

This is a **reference collector set**, not automatically applied to the NVIDIA
DCGM Exporter deployment.

It adds fields useful for LLM and P/D analysis beyond the common Better NVIDIA
DCGM Dashboard surface:

- NVLink TX/RX profiling throughput
- NVLink aggregate bandwidth
- NVLink P2P link status
- NVLink CRC/replay/recovery errors
- PCIe TX/RX throughput and replay count
- SM active / SM occupancy
- Tensor / FP16 / FP32 pipeline activity
- DRAM active ratio
- XID and GPU health
- ECC / retired pages / row remap state
- power / thermal / board / reliability throttle durations

Important: dcgm-exporter custom metric configuration is a **complete
replacement** for the default metric list, not an additive overlay. Review the
CSV against the exact GPU, driver, DCGM and exporter version before rollout.

Some profiling/DCP fields are hardware/version dependent. If a panel shows
`No data`, first check the "Advanced DCGM Metric Inventory" panel or the raw
exporter `/metrics` output before treating it as a Grafana query problem.

## NVLink evidence for P/D

The DCGM deep-dive dashboard intentionally separates three questions:

~~~text
1. Can these GPUs communicate over NVLink?
   -> DCGM_EXP_P2P_STATUS

2. Is NVLink traffic actually occurring?
   -> DCGM_FI_PROF_NVLINK_TX_BYTES
   -> DCGM_FI_PROF_NVLINK_RX_BYTES
   -> DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL

3. Is the fabric healthy while traffic occurs?
   -> CRC FLIT / CRC DATA
   -> replay
   -> recovery errors
~~~

For a controlled P/D test, correlate the request/KV-transfer interval with the
NVLink TX/RX spike and compare it with PCIe TX/RX.

This is objective **device-fabric activity evidence**, but it is node/GPU-level
telemetry rather than per-process attribution. Other simultaneous GPU workloads
can contribute traffic, so use an isolated test window when proving the P/D
path.

## Dashboard inventory

~~~text
grafana/dashboards/pd-cell-overview.json
  P/D request, latency, scheduler, KV cache and MTP analysis

grafana/dashboards/dcgm-llm-fabric-deep-dive.json
  NVLink/PCIe fabric, compute pipeline, throttle and reliability analysis
~~~

Every new panel includes a Korean Grafana panel description. Grafana renders the
description through the panel information tooltip so the dashboard itself
documents how to interpret the metric.


## Why short ranges previously showed No data

The first dashboard revision used `$__rate_interval`, but the supplied
Prometheus configuration did not declare `scrape_interval`.

Prometheus defaults to:

~~~text
scrape_interval = 1m
~~~

while Grafana Prometheus data sources commonly default to a 15s scrape interval.
Grafana calculates:

~~~text
$__rate_interval = max($__interval + scrape_interval, 4 * scrape_interval)
~~~

using the **Grafana data-source / query Min step value**, not by reading the
actual Prometheus server configuration.

That mismatch explains the observed pattern:

~~~text
Gauge panels
  -> work at Last 5m

rate()/histogram panels
  -> No data at Last 5m
  -> begin working only after dashboard range is expanded
~~~

PR #7 now makes the serving scrape contract explicit:

~~~yaml
scrape_interval: 15s
scrape_timeout: 10s
~~~

for standalone vLLM, P/D engines, P/D router and LiteLLM.

The P/D dashboard also sets a 15s Prometheus query Min step and uses two query
styles:

~~~text
continuous rate panels
  -> rate(...[$__rate_interval])

benchmark-range latency/token percentiles
  -> increase(histogram_bucket[$__range])
     + instant histogram_quantile(...)
~~~

The second form is intentionally used for TTFT/ITL/TPOT/E2E/queue/phase/token
percentiles so **Last 5m means "calculate the percentile from requests observed
in these 5 minutes"**, instead of depending on a rolling rate window.

If there are genuinely zero request samples in the selected range, these stat
panels render a Korean `최근 요청 없음` / `최근 Decode 표본 없음` message rather
than being interpreted as a broken dashboard.

### vLLM 0.26.0 metric audit

The P/D dashboard was re-audited against
`vllm-project/vllm v0.26.0` source.

The following speculative-decoding counters are valid:

~~~text
vllm:spec_decode_num_drafts_total
vllm:spec_decode_num_draft_tokens_total
vllm:spec_decode_num_accepted_tokens_total
vllm:spec_decode_num_accepted_tokens_per_pos_total
~~~

The earlier draft referenced metrics not present in v0.26.0:

~~~text
vllm:spec_decode_num_emitted_tokens_total
vllm:spec_decode_efficiency
vllm:spec_decode_draft_acceptance_rate
~~~

Those references were removed.

The dashboard now computes the documented metrics directly:

~~~promql
acceptance_rate =
  accepted_tokens / draft_tokens

mean_acceptance_length =
  1 + accepted_tokens / num_drafts
~~~

MTP metric families are created only when speculative decoding is enabled, so
an MTP-disabled engine intentionally reports `MTP 비활성/표본 없음` in that
dashboard section.


## Throughput and latency query semantics

The P/D dashboard now uses two different rolling windows intentionally.

### Throughput

~~~promql
increase(counter[2m]) / 120
~~~

is used for:

- request throughput
- prompt token throughput
- generation token throughput

This is a rolling two-minute average. It is more useful for short, bursty manual
P/D tests than a zero-filled `rate()` query because:

- a short burst remains visible for the next two minutes;
- a missing counter/selector is not silently converted into zero;
- a real flat counter still evaluates to zero.

### Latency

Latency panels are historical time-series again.

Each point is a rolling five-minute percentile:

~~~promql
histogram_quantile(
  <quantile>,
  sum by (le, pd_role) (
    increase(<latency>_bucket[5m])
  )
)
~~~

Every latency panel includes:

~~~text
p50
p90
p95
~~~

This is intentionally independent from the Grafana dashboard range.

Examples:

~~~text
Dashboard = Last 5m
  -> see the historical rolling-5m latency series during those 5 minutes

Dashboard = Last 6h
  -> see the same rolling-5m latency definition across six hours
~~~

### Time-series tooltip ordering

All time-series panels use:

~~~json
"tooltip": {
  "mode": "multi",
  "sort": "none"
}
~~~

and legends use Name ascending.

Grafana's native time-series tooltip does not expose an explicit
`sort-by-series-name` option; it only exposes None / value Ascending / value
Descending. The dashboard therefore disables value sorting and keeps fixed
query/legend names in alphabetical order so the tooltip follows stable
name-oriented series ordering instead of being reordered by the current value.
