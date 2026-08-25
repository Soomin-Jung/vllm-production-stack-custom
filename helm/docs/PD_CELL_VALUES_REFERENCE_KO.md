# P/D Cell Values Reference — custom vLLM Production Stack 0.1.8

## 핵심 규칙

P/D Cell 내부 orchestrator는 **항상 `vllm-project/router`**다.

다음 field는 더 이상 지원하지 않는다.

```yaml
pdCellSpec.router.type
pdCellSpec.router.args
pdCellSpec.router.kvConnector
```

- Global `routerSpec`는 기존 LMStack Router용으로 유지 가능
- Cell Router image는 `pdCellSpec.router.repository/tag`에서 별도로 명시
- KV connector는 `models[].kvTransfer.connector`에서 선언
- Helm이 vLLM Router `--kv-connector`를 자동 결정

---

## 최상위 구조

```yaml
pdCellSpec:
  enabled: true
  router: {...}
  models:
    - name: ...
      repository: ...
      tag: ...
      kvTransfer: {...}
      prefill: {...}
      decode: {...}
```

---

## `pdCellSpec`

| field | 기본값 | 설명 |
|---|---|---|
| `enabled` | `false` | P/D Cell 리소스 생성 여부 |
| `imagePullPolicy` | `servingEngineSpec.imagePullPolicy` | P/D engine image pull policy |
| `runtimeClassName` | `servingEngineSpec.runtimeClassName` | Pod RuntimeClass |
| `schedulerName` | `servingEngineSpec.schedulerName` | Kubernetes scheduler |
| `imagePullSecret` | empty | image pull secret |
| `serviceAccountName` | empty | ServiceAccount |
| `priorityClassName` | empty | PriorityClass |
| `progressDeadlineSeconds` | `1800` | Deployment progress deadline |
| `terminationGracePeriodSeconds` | `60` | Pod termination grace |
| `strategy` | RollingUpdate, maxSurge 0/maxUnavailable 1 | Deployment strategy |
| `podAnnotations` | `{}` | Pod annotations |
| `securityContext` | `{}` | Pod securityContext |
| `containerSecurityContext` | `{}` | P/D engine common container securityContext |
| `env` | `[]` | Cell common env |
| `envFrom` | `[]` | Cell common envFrom |
| `extraVolumes` | `[]` | Cell common volumes |
| `extraVolumeMounts` | `[]` | Cell common mounts |
| `nodeName` | empty | direct node pin |
| `nodeSelectorTerms` | `[]` | required node affinity terms |
| `affinity` | `{}` | explicit affinity; 있으면 nodeSelectorTerms보다 우선 |
| `tolerations` | `[]` | tolerations |
| `serviceType` | `ClusterIP` | Cell service type |
| `servicePort` | `servingEngineSpec.servicePort` | Cell service port |
| `serviceAnnotations` | `{}` | Cell service annotations |

상속 우선순위는 일반적으로:

```text
global / servingEngineSpec
  -> pdCellSpec
  -> models[]
  -> router/prefill/decode
```

단, **Global `routerSpec`의 image/tag는 Cell Router로 상속하지 않는다.** Global Router와 Cell orchestrator 구현이 다르기 때문이다.

---

## `pdCellSpec.router`

Cell Router는 vLLM Router 전용 설정이다.

```yaml
router:
  repository: registry.internal/vllm/vllm-router
  tag: v0.1.15
  port: 8000
  policy: consistent_hash
  prometheusPort: 29000
```

### 필수

| field | 설명 |
|---|---|
| `repository` | vllm-project/router 기반 image repository |
| `tag` | 검증한 version/tag. Mooncake baseline은 `v0.1.15` |

### 선택

| field | 기본값 | 설명 |
|---|---|---|
| `imagePullPolicy` | `IfNotPresent` | Router image pull policy |
| `port` | `8000` | Router API port |
| `policy` | `consistent_hash` | vLLM Router routing policy |
| `prometheusPort` | `29000` | Router metrics port. `0`이면 별도 metrics port 비활성 |
| `healthPath` | `/health` | Kubernetes probe path |
| `healthCheckInterval` | `30` | `--health-check-interval-secs` |
| `healthCheckTimeout` | `5` | `--health-check-timeout-secs` |
| `startupProbeInitialDelaySeconds` | `5` | Router startup probe |
| `startupProbePeriodSeconds` | `5` | Router startup probe |
| `startupProbeFailureThreshold` | `180` | Mooncake bootstrap wait 포함 약 15분 기본 budget |
| `livenessProbePeriodSeconds` | `10` | liveness period |
| `livenessProbeFailureThreshold` | `3` | liveness failures |
| `readinessProbePeriodSeconds` | `5` | readiness period |
| `readinessProbeFailureThreshold` | `3` | readiness failures |
| `resources` | CPU 1000m / memory 5Gi request | Router resources |
| `command` | `[vllm-router]` | 실행파일 path override만 허용 |
| `extraArgs` | `[]` | fixed P/D args 뒤에 추가할 Router option |
| `env` | `[]` | Router env |
| `envFrom` | `[]` | Router envFrom |
| `extraVolumes` | `[]` | Router-only volume definition |
| `extraVolumeMounts` | `[]` | Router-only mount |
| `containerSecurityContext` | `{}` | Router securityContext |

### 제거된 field

`router.type`:

```text
제거 이유: Cell 내부 Router implementation을 vllm-router로 고정
```

`router.args`:

```text
제거 이유: full override가 --vllm-pd-disaggregation / connector contract를 우회할 수 있음
```

`router.kvConnector`:

```text
제거 이유: engine connector와 Router connector가 분리 설정되면 protocol mismatch 가능
```

---

## 생성되는 Router command

Mooncake P1D2 예:

```text
command: [vllm-router]

--host 0.0.0.0
--port 8000
--policy consistent_hash
--vllm-pd-disaggregation
--kv-connector mooncake
--health-check-interval-secs 30
--health-check-timeout-secs 5
--prefill http://127.0.0.1:8101 9001
--decode http://127.0.0.1:8201
--decode http://127.0.0.1:8202
--prometheus-host 0.0.0.0
--prometheus-port 29000
```

NIXL에서는 bootstrap port를 `--prefill` 뒤에 붙이지 않는다.

---

## `models[]`

| field | 필수 | 설명 |
|---|---|---|
| `name` | O | Kubernetes resource identity. release 안에서 unique |
| `repository` | O | P/D vLLM image |
| `tag` | O | P/D image tag |
| `servedModelNames` | X | `[primary, alias...]`; 생략하면 `name` 사용 |
| `servedModelName` | X | 구 values 호환. 새 values는 `servedModelNames` 권장 |
| `replicaCount` | X | 기본 1, 0 이상 |
| `modelType` | X | metadata용, 기본 chat |
| `kvTransfer` | O | 모델별 KV transfer 설정 |
| `prefill` | O | Prefill topology |
| `decode` | O | Decode topology |
| `router` | X | 이 모델에서만 Router image/resource/probe/env override |

모델 단위로 `imagePullPolicy`, scheduling, service, env/mount/security 값을 override할 수 있다.

---

## `models[].kvTransfer`

KV transfer는 모델/profile/topology에 종속되므로 `pdCellSpec` 공통값으로 상속하지 않는다.

```yaml
kvTransfer:
  connector: MooncakeConnector
  bootstrapPortBase: 9001
  abortRequestTimeout: 600
  config:
    kv_load_failure_policy: fail
    kv_connector_extra_config:
      mooncake_protocol: nvlink_intra
      num_workers: 16
  prefillConfig: {}
  decodeConfig: {}
```

### field

| field | 기본값 | 설명 |
|---|---|---|
| `connector` | 필수 | vLLM engine KV connector class |
| `config` | `{}` | P/D 공통 `KVTransferConfig` |
| `prefillConfig` | `{}` | Prefill merge override |
| `decodeConfig` | `{}` | Decode merge override |
| `bootstrapPortBase` | `9001` | Mooncake Prefill bootstrap base |
| `abortRequestTimeout` | `600` | Mooncake abort timeout env |
| `nixlSideChannelEnabled` | connector 자동 판정 | MultiConnector 등에서 NIXL side channel 강제 시 사용 |

Helm이 마지막에 강제로 설정:

```text
Prefill kv_role = kv_producer
Decode  kv_role = kv_consumer
kv_connector    = connector field
```

---

## Router connector 자동 매핑

| `models[].kvTransfer.connector` | Router mode |
|---|---|
| 이름에 `Mooncake` 포함 | `mooncake` |
| 이름에 `Nixl` 포함 | `nixl` |
| 이름에 `Mori` 또는 `MoRI` 포함 | `moriio` |
| 그 외 | Helm render fail |

현재 고객사 사용값:

```yaml
connector: MooncakeConnector
```

---

## Mooncake 전용 env

Prefill:

```text
VLLM_MOONCAKE_BOOTSTRAP_PORT=9001+index
VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=600
```

Decode:

```text
VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=600
```

Decode에는 bootstrap server를 띄우지 않는다.

---

## NIXL 전용 env

NIXL connector일 때:

```text
VLLM_NIXL_SIDE_CHANNEL_PORT
```

을 phase/index별로 고유하게 생성한다.

`internalPortMode`:

| 값 | env |
|---|---|
| `vllm` | `VLLM_PORT` |
| `dp` | `VLLM_DP_MASTER_PORT` |
| `auto` | 둘 다 강제하지 않음 |

---

## API key

`servingEngineSpec.vllmApiKey`가 있으면:

P/D engine:

```text
VLLM_API_KEY
```

Cell Router:

```text
VLLM_API_KEY
OPENAI_API_KEY
```

두 Router env는 같은 secret을 가리킨다.

---

## `prefill` / `decode`

공통 핵심 field:

| field | 기본값 | 설명 |
|---|---|---|
| `count` | 필수 | phase container 수 |
| `requestGPU` | 필수 | container GPU reservation |
| `profile` | 필수 | vLLM `--config` path |
| `portBase` | P 8101 / D 8201 | HTTP base |
| `internalPortMode` | `vllm` | `vllm|dp|auto` |
| `internalPortBase` | P 20000 / D 30000 | internal port |
| `internalPortStride` | `100` | index stride |
| `dpMasterPortBase` | P 24000 / D 34000 | DP master |
| `sideChannelPortBase` | P 5600 / D 5700 | NIXL side channel |
| `command` | `[vllm, serve]` 계열 | engine command override |
| `extraArgs` | `[]` | engine extra flags |
| `env` | `[]` | phase env |
| `envFrom` | `[]` | phase envFrom |
| `extraVolumes` | `[]` | phase volumes |
| `extraVolumeMounts` | `[]` | phase mounts |
| `containerSecurityContext` | `{}` | phase security |
| `kvTransferConfig` | `{}` | final phase KV override |

CPU/memory resources는 기존 chart `chart.resources` helper를 사용한다.

---

## served model alias

```yaml
servedModelNames:
  - Qwen3.6-27B-PD
  - test-alias
```

P/D vLLM 모두 같은 이름 목록을 받는다.

Cell vLLM Router `/v1/models`는 Prefill `/v1/models`를 proxy하므로 alias가 catalog에도 보이는 것이 기대 contract다.

Global Router가 이 `/v1/models`를 discovery에 사용한다면 alias도 Global layer까지 노출되어야 한다.

---

## Service

모델마다:

```text
<release>-<name>-engine-service
```

를 생성한다.

Service target:

```text
pd-router named port -> Cell Router :8000
```

P/D engine port를 외부 service로 직접 노출하지 않는다.

---

## Metrics

Cell Router:

```text
29000/metrics
```

Prefill/Decode:

```text
8101+/metrics
8201+/metrics
```

기존 Prometheus relabel이 `container_port=8000`만 선택하면 P/D와 Router metrics port 29000을 놓치므로 별도 scrape rule 보완이 필요하다.

---

## 운영 금지/주의

### 금지

```yaml
router:
  type: lmstack
```

Cell 내부 LMStack orchestrator는 Mooncake contract mismatch 때문에 제거했다.

### 주의

Mooncake direct URL Router는 Prefill bootstrap에서 얻은 `engine_id`를 사용한다. Prefill만 container restart되어 engine_id가 변경되면 Router metadata refresh 여부를 확인해야 한다.

자동 refresh가 검증되지 않은 단기 운영에서는 P/D process failure 시 Cell Pod 전체 재생성을 권장한다.

---

## 권장 Mooncake baseline

```yaml
pdCellSpec:
  enabled: true
  router:
    repository: registry.internal/vllm/vllm-router
    tag: v0.1.15
    policy: consistent_hash
    prometheusPort: 29000

  models:
    - name: qwen-p1d2
      servedModelNames: [Qwen3.6-27B-PD, test-alias]
      repository: registry.internal/vllm/vllm-openai
      tag: v0.26.0-cu129-mooncake-0.3.10-post2
      kvTransfer:
        connector: MooncakeConnector
        bootstrapPortBase: 9001
        config:
          kv_load_failure_policy: fail
          kv_connector_extra_config:
            mooncake_protocol: nvlink_intra
      prefill:
        count: 1
        requestGPU: 4
        profile: /profiles/qwen/prefill-tp4.yaml
      decode:
        count: 2
        requestGPU: 2
        profile: /profiles/qwen/decode-tp2.yaml
```
