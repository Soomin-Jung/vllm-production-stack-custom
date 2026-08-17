# vLLM Production Stack: reference stack for production vLLM deployment

> **Internal downstream production baseline**  
> This repository preserves the upstream `vllm-project/production-stack` Git history and layers the currently operated vLLM Production Stack 0.1.8 customizations on top of the official release.

## Production 0.1.8 downstream baseline

### Upstream base

- Upstream project: `vllm-project/production-stack`
- Upstream tag: `vllm-stack-0.1.8`
- Upstream commit: `a2576d6f54d244c08e810e0e7584f17d7c22509a`
- The `vllm-stack-0.1.8` tag in this repository intentionally points to the same upstream commit.
- Downstream changes are kept as separate commits so that a future 0.1.12+ migration can classify each change as **KEEP / UPSTREAM_REPLACED / REIMPLEMENT / DROP**.

### Production values layering

The production environment does not rely on one monolithic values file. Common runtime values and model-specific deployment values are layered in Helm order:

```bash
helm install lln \
  -n inference \
  ./vllm-stack-0.1.8/ \
  -f ./vllm-stack-0.1.8/values.yaml \
  -f global-values.yaml \
  -f deploy-models.yaml
```

Within this repository, `helm/` is the upstream chart directory corresponding to the offline `./vllm-stack-0.1.8/` chart directory. Later values files override earlier values for the same key.

```text
upstream/chart defaults
        ↓
helm/values.yaml                 production chart baseline
        ↓
global-values.yaml               shared env / volumes / mounts
        ↓
deploy-models.yaml               model-specific final overrides
        ↓
/profiles/*.yaml                 vLLM runtime profiles referenced through extraArgs
```

`deploy-models.yaml` and the production profile set are separate operational inputs and are not yet captured by this baseline commit series.

### Downstream change map

| Area | Main files | Production behavior | Commit |
| --- | --- | --- | --- |
| Production chart defaults and router scheduling | `helm/values.yaml`, `helm/templates/deployment-router.yaml`, `helm/.helmignore` | Production labels, 60-minute startup window, GPU toleration, `gpu-binpack-scheduler`, pinned router image, router HA/HPA, LoadBalancer `:9400`, router resource limits, backup YAML exclusion | [`591f602`](../../commit/591f6021ea9d0f3f9975d469a4d7fe5c741e18c2) |
| GPU-derived resource policy | `helm/templates/_helpers.tpl` | `requestGPU` is mandatory as a key, `0` is allowed, negatives are rejected; default request is 4 CPU cores + 10Gi memory per GPU; explicit CPU/memory values win | [`f9efdf7`](../../commit/f9efdf712a68ca7193cad4e75e3f2e96bfaa561c) |
| Non-Ray vLLM runtime externalization | `helm/templates/deployment-vllm-multi.yaml` | Simplified Deployment name, global env/volume merge, profile-driven `vllm serve` arguments through `extraArgs`, legacy LMCache hook retained, `/dev/shm` always mounted from host | [`94ccabc`](../../commit/94ccabccc7e177431d0b7c257190bd567cdf681e) |
| Ray runtime integration | `helm/templates/ray-cluster.yaml` | Global env/volume merge for head and workers, model-specific values override globals by `name`, Ray shm default 20Gi → 100Gi, entrypoint no longer passes `modelURL` directly | [`27f2966`](../../commit/27f2966b9fb5f19ded395cd9afdb9da1387b3ae3) |
| Shared production globals | `global-values.yaml` | Offline Hugging Face mode, vLLM/HF cache paths, Responses store/stats settings, shared model/profile/cache hostPath mounts, timezone, legacy LMCache host IP | [`a2dddf7`](../../commit/a2dddf73b4a8331fcaa8b5f10f3396c67ed744d0) |
| Baseline regression validation | `.github/workflows/downstream-baseline-validation.yml` | Helm lint/template smoke tests for non-Ray/Ray, `requestGPU: 0`, global merge, profile args, host shm, Ray shm 100Gi, and GPU-derived resource fallbacks | [`4917f7a`](../../commit/4917f7a4d66074cb345ec6799b3838b37dc384ae) |

### Key runtime design

#### Global values + per-model override

`global.env`, `global.extraVolumes`, and `global.extraVolumeMounts` are converted to name-keyed maps in the serving templates. Per-model values are merged with `mergeOverwrite`; when the same `name` exists at both levels, the **model-specific value wins**. `deepCopy` is used before merging so one model's override does not mutate the shared global map and leak into another model.

#### Profile-driven vLLM configuration

For non-Ray deployments, the chart no longer owns every vLLM CLI option. The stable command skeleton is:

```text
vllm serve --host 0.0.0.0 --port <container-port> <vllmConfig.extraArgs...>
```

The production convention is to pass the model profile through `vllmConfig.extraArgs`, for example `--config /profiles/<profile>.yaml`. Model paths and engine options therefore live in profile YAML rather than requiring a Helm template change whenever vLLM adds a new CLI option.

Ray follows the same profile direction for the model path: the generated `vllm-entrypoint.sh` no longer injects `modelSpec.modelURL` as the positional model argument. The remaining Ray command construction is retained from the 0.1.8 baseline.

### Known operational notes / technical debt

- **`values.schema.json` does not match the customized resource helper.** Upstream 0.1.8 still requires `requestCPU`, `requestMemory`, and `pvcStorage` for every `modelSpec`. Therefore the GPU-derived CPU/memory fallback cannot be reached through normal schema validation when those keys are omitted, unless the schema is later updated or schema validation is bypassed. CI tests both the upstream-schema path and the helper fallback separately.
- **`requestGPU: 0` is intentionally supported** for special shared-GPU workloads such as embedding/reranker deployments. If CPU/memory are also omitted, the fallback becomes `0m` / `0Gi`; those workloads should explicitly provide CPU and memory requests.
- **Non-Ray `/dev/shm` uses hostPath `/dev/shm`.** This removes the prior TP-only mount condition and exposes host shared-memory capacity to every vLLM pod, but also reduces pod-level isolation and allows same-node workloads to contend for host shared memory.
- **Ray `/dev/shm` remains memory-backed `emptyDir`**, with the default raised from `20Gi` to `100Gi`; `modelSpec.shmSize` can still override it.
- **LMCache is legacy-disabled in the current production path.** The template hook remains, but current model deployments use `lmcacheConfig.enabled: false`. `LMCACHE_IP=status.hostIP` remains in global values from the standalone LMCache experiment and is currently unused.
- **`VLLM_ALLOW_RUNTIME_LORA_UPDATING=1` is still present for the current baseline.** LoRA adapters are not supported in the current production environment, and this setting prevents use of `api_server_count > 1`; removing it requires a coordinated vLLM pod restart.
- **Router limits vary by environment.** This baseline records `cpu: 1000m` and `memory: 5Gi`; some deployments intentionally override `routerSpec.resources.limits` with `{}`.
- Host paths in `global-values.yaml` represent the normalized intended production paths. Confirm them against the offline node filesystem before using this repository as a deployment source.

### Validation

The downstream workflow runs:

1. `helm lint` using schema-compliant synthetic non-Ray and Ray models.
2. `helm template` and rendered invariant checks for global env/volume merging, profile arguments, non-Ray host `/dev/shm`, Ray `100Gi` shm, and `requestGPU: 0`.
3. A separate render with the upstream schema temporarily excluded to exercise the customized GPU-derived CPU/memory fallback (`2 GPU → 8000m / 20Gi`).

---

## Upstream README

The content below is retained from the official vLLM Production Stack 0.1.8 repository.

| [**Blog**](https://lmcache.github.io) | [**Docs**](https://docs.vllm.ai/projects/production-stack) | [**Production-Stack Slack Channel**](https://vllm-dev.slack.com/archives/C089SMEAKRA) | [**LMCache Slack**](https://join.slack.com/t/lmcacheworkspace/shared_invite/zt-2viziwhue-5Amprc9k5hcIdXT7XevTaQ) | [**Interest Form**](https://forms.gle/mQfQDUXbKfp2St1z7) | [**Official Email**](contact@lmcache.ai) |

## Latest News

- 📄 [Official documentation](https://docs.vllm.ai/projects/production-stack) released for production-stack!
- ✨ [Cloud Deployment Tutorials](https://github.com/vllm-project/production-stack/blob/main/tutorials) for Lambda Labs, AWS EKS, Google GCP are out!
- 🛤️ 2025 Q1 roadmap is released! [Join the discussion now](https://github.com/vllm-project/production-stack/issues/26)!
- 🔥 vLLM Production Stack is released! Check out our [release blogs](https://blog.lmcache.ai/2025-01-21-stack-release) posted on January 22, 2025.

## Community Events

We host **bi-weekly** community meetings at the following timeslot:

- Every other Tuesdays at 5:30 PM PT – [Add to Calendar](https://drive.usercontent.google.com/u/0/uc?id=1I3WuivUVAq1vZ2XSW4rmqgD5c0bQcxE0&export=download)

All are welcome to join!

## Introduction

**vLLM Production Stack** project provides a reference implementation on how to build an inference stack on top of vLLM, which allows you to:

- 🚀 Scale from a single vLLM instance to a distributed vLLM deployment without changing any application code
- 💻 Monitor the metrics through a web dashboard
- 😄 Enjoy the performance benefits brought by request routing and KV cache offloading

## Step-By-Step Tutorials

0. How To [*Install Kubernetes (kubectl, helm, minikube, etc)*](https://github.com/vllm-project/production-stack/blob/main/tutorials/00-install-kubernetes-env.md)?
1. How to [*Deploy Production Stack on Major Cloud Platforms (AWS, GCP, Lambda Labs, Azure)*](https://github.com/vllm-project/production-stack/blob/main/tutorials/cloud_deployments)?
2. How To [*Set up a Minimal vLLM Production Stack*](https://github.com/vllm-project/production-stack/blob/main/tutorials/01-minimal-helm-installation.md)?
3. How To [*Customize vLLM Configs (optional)*](https://github.com/vllm-project/production-stack/blob/main/tutorials/02-basic-vllm-config.md)?
4. How to [*Load Your LLM Weights*](https://github.com/vllm-project/production-stack/blob/main/tutorials/03-load-model-from-pv.md)?
5. How to [*Launch Different LLMs in vLLM Production Stack*](https://github.com/vllm-project/production-stack/blob/main/tutorials/04-launch-multiple-model.md)?
6. How to [*Enable KV Cache Offloading with LMCache*](https://github.com/vllm-project/production-stack/blob/main/tutorials/05-offload-kv-cache.md)?

## Architecture

The stack is set up using [Helm](https://helm.sh/docs/), and contains the following key parts:

- **Serving engine**: The vLLM engines that run different LLMs.
- **Request router**: Directs requests to appropriate backends based on routing keys or session IDs to maximize KV cache reuse.
- **Observability stack**: monitors the metrics of the backends through [Prometheus](https://github.com/prometheus/prometheus) + [Grafana](https://grafana.com/)

<p align="center">
  <img src="https://github.com/user-attachments/assets/8f05e7b9-0513-40a9-9ba9-2d3acca77c0c" alt="Architecture of the stack" width="80%"/>
</p>

## Roadmap

We are actively working on this project and will release the following features soon. Please stay tuned!

- **Autoscaling** based on vLLM-specific metrics
- Support for **disaggregated prefill**
- **Router improvements** (e.g., more performant router using non-python languages, KV-cache-aware routing algorithm, better fault tolerance, etc)

## Deploying the stack via Helm

### Prerequisites

- A running Kubernetes (K8s) environment with GPUs
  - Run `cd utils && bash install-minikube-cluster.sh`
  - Or follow our [tutorial](tutorials/00-install-kubernetes-env.md)

### Deployment

vLLM Production Stack can be deployed via helm charts. Clone the repo to local and execute the following commands for a minimal deployment:

```bash
git clone https://github.com/vllm-project/production-stack.git
cd production-stack/
helm repo add vllm https://vllm-project.github.io/production-stack
helm install vllm vllm/vllm-stack -f tutorials/assets/values-01-minimal-example.yaml
```

The deployed stack provides the same [**OpenAI API interface**](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html?ref=blog.mozilla.ai#openai-compatible-server) as vLLM, and can be accessed through kubernetes service.

To validate the installation and send a query to the stack, refer to [this tutorial](tutorials/01-minimal-helm-installation.md).

For more information about customizing the helm chart, please refer to [values.yaml](https://github.com/vllm-project/production-stack/blob/main/helm/values.yaml) and our other [tutorials](https://github.com/vllm-project/production-stack/tree/main/tutorials).

### Uninstall

```bash
helm uninstall vllm
```

## Grafana Dashboard

### Features

The Grafana dashboard provides the following insights:

1. **Available vLLM Instances**: Displays the number of healthy instances.
2. **Request Latency Distribution**: Monitors end-to-end response times.
3. **Time-to-First-Token (TTFT) Distribution**: Monitors response times for token generation.
4. **Number of Running Requests**: Tracks the number of active requests per instance.
5. **Number of Pending Requests**: Tracks requests waiting to be processed.
6. **GPU KV Usage Percent**: Monitors GPU KV cache usage.
7. **GPU KV Cache Hit Rate**: Displays the hit rate for the GPU KV cache.

<p align="center">
  <img src="https://github.com/user-attachments/assets/05766673-c449-4094-bdc8-dea6ac28cb79" alt="Grafana dashboard to monitor the deployment" width="80%"/>
</p>

### Configuration

See the details in [`observability/README.md`](./observability/README.md)

## Router

The router ensures efficient request distribution among backends. It supports:

- Routing to endpoints that run different models
- Exporting observability metrics for each serving engine instance, including QPS, time-to-first-token (TTFT), number of pending/running/finished requests, and uptime
- Automatic service discovery and fault tolerance via the Kubernetes API
- Model aliases
- Multiple routing algorithms:
  - Round-robin routing
  - Session-ID based routing
  - Prefix-aware routing (WIP)

Please refer to the [router documentation](./src/vllm_router/README.md) for more details.

## Contributing

We welcome and value any contributions and collaborations. Please check out [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved.

## License

This project is licensed under Apache License 2.0. See the `LICENSE` file for details.

## Sponsors

We are grateful to our sponsors who support our development and benchmarking efforts:

<p align="center">
  <a href="https://gmicloud.ai">
    <img src="https://cdn.prod.website-files.com/6683d8c52e4e62685a8d90cf/67a0a0064683945b0cf77f25_GMI%20Cloud%20Logo_Black.svg" alt="GMI Cloud Logo" width="200"/>
  </a>
</p>

---

For any issues or questions, feel free to open an issue or contact us ([@ApostaC](https://github.com/ApostaC), [@YuhanLiu11](https://github.com/YuhanLiu11), [@Shaoting-Feng](https://github.com/Shaoting-Feng)).
