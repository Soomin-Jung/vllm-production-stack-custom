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
