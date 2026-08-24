# vLLM Production Stack 0.1.8 - Node-local P/D Cell 구현 가이드

이 문서는 `agent/production-0.1.8-baseline-final`을 기준으로 추가된 단기 P/D Cell 확장의 구조와 values → template → Kubernetes resource → runtime 연결 관계를 설명한다.

> 목표는 기존 integrated/Ray 배포를 건드리지 않고, 0.1.12 전면 이관 전에 실제 P/D Disaggregation을 node-local Cell 단위로 배포·확장·관측할 수 있게 만드는 것이다.

---

## 1. 왜 별도 `pdCellSpec`인가

현재 0.1.8 baseline의 `servingEngineSpec.modelSpec`은 이미 다음 renderer가 직접 소비한다.

```text
servingEngineSpec.modelSpec
  ├─ deployment-vllm-multi.yaml
  ├─ service-vllm.yaml
  └─ ray-cluster.yaml
```

특히 `deployment-vllm-multi.yaml`과 `ray-cluster.yaml`은 `raySpec` 존재 여부를 분기 기준으로 사용한다.

단기 P/D 기능을 같은 배열에 바로 넣으려면 기존 Deployment/Service/Ray template을 함께 수정해야 하고, 운영 중인 integrated 모델 manifest에 regression이 생길 수 있다.

따라서 단기 0.1.8에서는 다음처럼 **additive root**를 사용한다.

```yaml
pdCellSpec:
  enabled: true
  models:
    - name: example-pd
      ...
```

여기서도 **Prefill과 Decode는 별도 모델이 아니다.** 한 모델 block 안에서 실행 topology로 표현한다.

장기 0.1.12+에서는 이 block을 다음 계열로 semantic migration한다.

```yaml
servingEngineSpec:
  modelSpec:
    - name: example
      deploymentMode: disaggregated
      disaggregatedServing:
        topology: nodeLocal
        ...
```

즉 0.1.8에서 별도 root를 쓰는 이유는 단기 안전성이지, P/D를 별도 플랫폼으로 만들기 위한 것이 아니다.

---

## 2. 생성되는 전체 구조

실제 운영 values에는 필수값만 적는다.

```yaml
pdCellSpec:
  enabled: true

  models:
    - name: example-pd-p2d2
      servedModelNames:
        - example-model
        - example-alias
      repository: vllm/vllm-openai
      tag: v0.27.1-cu129
      replicaCount: 1

      kvTransfer:
        connector: NixlConnector

      prefill:
        count: 2
        requestGPU: 2
        profile: /profiles/example/pd-prefill.yaml

      decode:
        count: 2
        requestGPU: 2
        profile: /profiles/example/pd-decode.yaml
```

Helm은 모델 block 하나에서 다음 리소스를 만든다.

```text
Deployment/<release>-example-pd-p2d2-pd-cell
Service/<release>-example-pd-p2d2-engine-service
```

Deployment Pod template은 다음과 같다.

```text
P/D Cell Pod

┌──────────────────────────────────────┐
│ pd-router                  :8000     │
│                                      │
│ prefill-0                  :8101     │
│ prefill-1                  :8102     │
│                                      │
│ decode-0                   :8201     │
│ decode-1                   :8202     │
│                                      │
│ connector/internal ports   implicit  │
└──────────────────────────────────────┘
```

각 P/D engine이 GPU 2개를 요청하면 Pod 전체 request는 8 GPU가 된다.

```text
P0 2 GPU
P1 2 GPU
D0 2 GPU
D1 2 GPU
────────
Cell 8 GPU
```

Kubernetes는 Pod를 여러 Node에 나누어 배치하지 않으므로, GPU 8개를 요청한 Cell Pod는 GPU 8개를 수용할 수 있는 한 Node에 통째로 배치된다.

따라서 `replicaCount: 3`이면 Cell 세 개를 scheduler가 서로 가용한 Node에 배치한다. `replicaCount: 0`도 허용하므로 여러 topology를 values에 유지한 채 필요한 topology만 활성화할 수 있다.

기본 운영에서는 `nodeName`을 지정하지 않는다.

---

## 3. Values와 Template 연결

### 3.1 최상위

```yaml
pdCellSpec:
  enabled: true
  router: {...}
  models: [...]
```

연결:

```text
pdCellSpec.enabled
   ├─ deployment-pd-cell.yaml 활성화
   └─ service-pd-cell.yaml 활성화

pdCellSpec.models[]
   └─ 모델마다 Deployment + Service 1세트 생성
```

`pdCellSpec` 최상위 값은 모든 `models[]`의 공통 기본값이다. 모델 항목에 같은 field를 선언하면 모델 값이 우선한다.

```text
global / servingEngineSpec 기본값
  → pdCellSpec 공통값
    → models[] 모델별 override
      → prefill/decode phase override
```

공통으로 둘 수 있는 주요 field:

| 분류 | `pdCellSpec` 공통 field |
|---|---|
| Runtime | `imagePullPolicy`, `runtimeClassName`, `schedulerName`, `imagePullSecret` |
| Environment | `env`, `extraVolumes`, `extraVolumeMounts` |
| Scheduling | `nodeName`, `nodeSelectorTerms`, `affinity`, `tolerations` |
| Service | `serviceType`, `servicePort`, `serviceAnnotations` |
| Pod metadata | `podAnnotations` |
| Router common | `router` |

`kvTransfer`는 connector와 세부 config가 모델·profile·GPU topology 호환성에 종속되므로 공통값으로 상속하지 않고 `models[].kvTransfer`에 모델별로 선언한다.

생략 시 기존 baseline을 재사용한다.

| 생략한 값 | 실제 기본 동작 |
|---|---|
| Engine `imagePullPolicy` | `servingEngineSpec.imagePullPolicy` |
| `runtimeClassName` | `servingEngineSpec.runtimeClassName` |
| `schedulerName` | `servingEngineSpec.schedulerName` |
| `tolerations` | `servingEngineSpec.tolerations`를 항상 포함. 현재 GPU `NoSchedule` toleration도 자동 상속 |
| Engine `requestCPU` | `requestGPU` 1개당 4 CPU, 즉 `4000m × requestGPU` |
| Engine `requestMemory` | `requestGPU` 1개당 `10Gi` |
| Router resources | `routerSpec.resources`. 현재 baseline은 request `1000m/5Gi`, memory limit `5Gi` |
| Engine HTTP port | Prefill `8101+index`, Decode `8201+index` |
| Router/Service | Router `8000`, Service `ClusterIP`, Service port는 `servingEngineSpec.servicePort` |

따라서 `requestGPU: 2`만 적으면 engine container 하나당 CPU `8000m`, memory `20Gi`가 요청된다. 더 필요할 때만 phase의 `requestCPU`, `requestMemory`를 명시한다.

---

### 3.2 모델 identity

```yaml
- name: example-pd-p2d2
  servedModelName: example-model
  replicaCount: 2
```

의미:

| field | 의미 |
|---|---|
| `name` | Kubernetes 리소스 identity / Cell deployment name. 한 Helm release의 `models[]` 안에서 반드시 고유 |
| `servedModelNames` | P/D Router와 vLLM API에서 사용하는 model ID 목록. 첫 항목은 primary, 나머지는 alias |
| `servedModelName` | 이전 values 호환 field. string 또는 list를 받지만 새 values는 `servedModelNames` 권장 |
| `replicaCount` | P/D Cell 개수. `0` 이상 |

같은 모델을 다른 topology로 동시에 시험할 때는 `name`만 다르게 하고 `servedModelNames`와 profile을 공유해도 된다.

```yaml
models:
  - name: qwen-p1d1
    servedModelNames: [qwen-test, standard]
    prefill: {count: 1, requestGPU: 4, profile: /profiles/qwen-p.yaml}
    decode: {count: 1, requestGPU: 4, profile: /profiles/qwen-d.yaml}

  - name: qwen-p2d1
    servedModelNames: [qwen-test, standard]
    prefill: {count: 2, requestGPU: 4, profile: /profiles/qwen-p.yaml}
    decode: {count: 1, requestGPU: 4, profile: /profiles/qwen-d.yaml}

  - name: qwen-p2d2
    servedModelNames: [qwen-test, standard]
    prefill: {count: 2, requestGPU: 2, profile: /profiles/qwen-p-tp2.yaml}
    decode: {count: 2, requestGPU: 2, profile: /profiles/qwen-d-tp2.yaml}
```

여기서 `count`는 engine container 수이지 TP 크기가 아니다. profile의 TP/PP/DP가 요구하는 GPU 수와 `requestGPU`는 반드시 맞아야 한다.

주의할 routing 의미:

- 각 topology에는 `<release>-<name>-engine-service`가 따로 생기므로 Service로 직접 호출하면 topology별 테스트가 분리된다.
- Global Router가 같은 primary 또는 alias를 노출하는 Cell을 모두 발견하면 하나의 backend pool처럼 섞어 분산할 수 있다.
- 따라서 topology별 성능 비교는 각각의 생성 Service를 직접 사용하거나, 비교 기간에만 topology별 alias를 사용한다.
- 각 block의 Prefill/Decode profile 안 `served-model-name` list는 `servedModelNames`와 같은 순서로 맞춘다. vLLM 응답과 Prometheus `model_name`에는 첫 이름이 사용된다.

Helm은 외부 `/profiles` 파일 내용까지 읽을 수 없으므로 이 일치 여부는 배포 전 검증 항목이다.

---

### 3.3 vLLM image

```yaml
repository: vllm/vllm-openai
tag: v0.27.1-cu129
```

Prefill/Decode 모든 engine container가 같은 image를 사용한다.

선택한 connector의 runtime이 image에 포함되어 있어야 한다. 예를 들어 NIXL은 vLLM이 요구하는 NIXL package/build가, Mooncake는 Mooncake runtime/package가 필요하다.

---

### 3.4 Cell Router

```yaml
pdCellSpec:
  router:
    type: lmstack
    repository: lmcache/lmstack-router
    tag: validated-version
```

Router는 보통 모든 topology가 같은 image를 사용하므로 최상위에 한 번만 선언한다. 전부 생략하면 `routerSpec.repository/tag/resources`를 상속한다. 특정 모델만 다르게 검증할 때 `models[].router`로 일부 field를 override한다.

Router resource를 바꿀 때는 Kubernetes 표준 map을 사용한다.

```yaml
router:
  resources:
    requests:
      cpu: "2"
      memory: 4Gi
    limits:
      memory: 4Gi
```

Cell Router는 기존 Global Router와 별도 process다.

```text
Global LMRouter 0.1.8
  └─ Cell Router endpoint :8000
       ├─ Prefill pool
       └─ Decode pool
```

### Router 구현별 실행 계약

`lmcache/lmstack-router`와 `vllm-project/router`는 같은 프로그램이 아니며 CLI도 호환되지 않는다. image repository만 바꾸고 기존 args를 그대로 쓰면 안 된다.

기준 자료는 [Production Stack LMStack Router CLI](https://github.com/vllm-project/production-stack/blob/main/src/vllm_router/README.md)와 [vLLM Router PD CLI](https://github.com/vllm-project/router#prefill-decode-disaggregation)다.

| `router.type` | image 계열 | Helm이 생성하는 핵심 args |
|---|---|---|
| `lmstack` | `lmcache/lmstack-router`, production-stack `src/vllm_router` | `--service-discovery static`, `--static-backends/models/aliases`, `--routing-logic disaggregated_prefill_orchestrated` |
| `vllm` | `vllm-project/router` | `--vllm-pd-disaggregation`, 반복형 `--prefill/--decode`, `--kv-connector`, `--policy` |
| `custom` | 사내/기타 image | `router.command` 선택, `router.args` 필수 |

어떤 type이든 `router.args`를 명시하면 Helm generated args 전체를 대체하고 `router.extraArgs`는 마지막에 append한다.

LMStack Router image는 반드시 다음 기능이 검증된 image를 pin한다.

```text
disaggregated_prefill_orchestrated
static service discovery
static model labels
static backend health check
```

`latest` 사용은 권장하지 않는다.

vLLM Router의 NIXL 최소 예:

```yaml
router:
  type: vllm
  repository: registry.example/vllm-router
  tag: validated-version
  policy: consistent_hash
```

`vllm-project/router` 공식 README는 현재 `Dockerfile.router` 빌드와 실행 방법을 설명하지만 고정된 공식 public image 경로를 계약으로 제시하지 않는다. 따라서 해당 소스 revision으로 image를 빌드해 사내 registry에 push하고 digest 또는 검증 tag를 pin한다.

---

## 4. Cell Router의 실제 backend 생성

예:

```yaml
prefill:
  count: 2
  portBase: 8101

decode:
  count: 2
  portBase: 8201
```

Template이 생성하는 backend:

```text
Prefill
http://127.0.0.1:8101
http://127.0.0.1:8102

Decode
http://127.0.0.1:8201
http://127.0.0.1:8202
```

Cell Router에는 다음 의미의 인자가 자동 생성된다.

```text
--service-discovery static
--static-backends <P/D localhost endpoints>
--static-models <primary model name repeated>
--static-aliases <alias:primary mappings, alias가 있을 때>
--static-model-labels <prefill/decode role labels>
--routing-logic disaggregated_prefill_orchestrated
--prefill-model-labels <prefill label>
--decode-model-labels <decode label>
```

따라서 Cell Router는 자기 Pod 안의 P/D engine만 볼 수 있다.

다른 Node의 Prefill과 Decode가 pairing되는 경로가 존재하지 않는다.

---

## 5. Profile 연결

기존 운영의 profile 방식을 그대로 사용한다.

```yaml
prefill:
  profile: /profiles/example/pd-prefill.yaml

decode:
  profile: /profiles/example/pd-decode.yaml
```

Prefill engine 실행:

```text
vllm serve
  --host 0.0.0.0
  --port 8101
  --config /profiles/example/pd-prefill.yaml
  --served-model-name example-model example-alias
  --kv-transfer-config <selected connector + kv_producer>
```

Decode engine 실행:

```text
vllm serve
  --host 0.0.0.0
  --port 8201
  --config /profiles/example/pd-decode.yaml
  --served-model-name example-model example-alias
  --kv-transfer-config <selected connector + kv_consumer>
```

### 책임 경계

Profile:

- model path
- served-model-name
- dtype
- max-model-len
- kv-cache-dtype
- batching
- chunked prefill
- 기타 vLLM engine option

Helm:

- P/D count
- engine port
- GPU resource
- P/D role
- KV connector role
- connector별 side/bootstrap/internal port 충돌 방지
- Router membership
- Kubernetes scheduling

Helm은 `servedModelNames`를 하나의 `--served-model-name name alias...` CLI로 Prefill/Decode 모두에 명시한다. 따라서 profile YAML parser가 list를 문자열로 잘못 읽는 구버전 문제를 피하고 실제 runtime 이름을 topology values와 일치시킨다. profile의 `served-model-name`도 운영 가독성과 단독 실행 일관성을 위해 같은 값과 순서로 유지한다.

배포 acceptance에서 각 Cell Service의 `/v1/models`가 primary와 모든 alias를 각각 노출하고, 각 이름으로 실제 completion 요청이 성공하는지 확인한다.

### Prefill/Decode GPU 수가 다른 경우

`prefill.requestGPU: 4`, `decode.requestGPU: 2`처럼 container당 GPU 수가 달라도 Kubernetes resource 표현에는 문제가 없다. 단, `requestGPU`는 GPU를 예약할 뿐 vLLM tensor parallelism을 자동 설정하지 않으므로 Prefill profile은 TP4, Decode profile은 TP2처럼 실제 local parallelism과 맞춰야 한다.

vLLM 0.27.1의 [NixlConnector compatibility matrix](https://github.com/vllm-project/vllm/blob/v0.27.1/docs/features/nixl_connector_compatibility.md)에 따르면 Dense Transformer와 일반 MoE에서 heterogeneous TP를 지원한다. P/D 양쪽의 vLLM/NIXL version, model 구조, dtype, attention backend, KV cache dtype은 같아야 한다. 다음 제한도 적용한다.

- Hybrid SSM/Mamba는 heterogeneous TP를 아직 지원하지 않으므로 P TP와 D TP를 같게 한다.
- MLA는 heterogeneous TP를 지원하지만 KV가 TP worker에 복제되므로 일반 head splitting과 동작이 다르다.
- NHD layout은 heterogeneous TP head splitting을 지원하지 않는다.
- Pipeline Parallelism을 NixlConnector P/D에 섞는 구성은 별도 지원 상태를 확인한다.
- Mooncake 또는 custom connector는 해당 version의 compatibility를 별도로 검증한다.

즉 Qwen 계열이 Dense/MoE attention 모델이고 profile/runtime 조건을 맞췄다면 `Prefill 4 GPU × 1 + Decode 2 GPU × 2`가 가능한 topology다. Cell Pod의 총 GPU request는 `4 + 2×2 = 8`이다.

---

## 6. Global env / volume inheritance

현재 custom 0.1.8 baseline과 동일하게 `global.env`, `global.extraVolumes`, `global.extraVolumeMounts`를 모든 Cell container에 상속한다. PD Cell 전체 공통값은 `pdCellSpec`에 한 번만 둘 수 있다.

따라서 기존 `/profiles` mount나 공통 cache mount를 그대로 재사용할 수 있다.

우선순위:

```text
global env
   ↓
pdCellSpec env
   ↓
model env
   ↓
router 또는 phase(prefill/decode) env
   ↓
runtime-required env
```

mount는 `global → pdCellSpec → model → router/prefill/decode` 순서다. 따라서 router 전용 config는 `router.extraVolumeMounts`, Prefill/Decode 전용 cache는 각 phase의 `extraVolumeMounts`에 둔다.

volume은 Kubernetes Pod 단위이므로 `router/prefill/decode.extraVolumes`도 최종적으로 하나의 Pod volume 집합에 합쳐진다. 서로 다른 container 전용 volume은 고유한 `name`을 사용한다.

`envFrom`도 같은 계층을 지원하며 기존 `models[].envFromSecret.name` 단축 문법은 모든 Cell container에 적용된다.

---

## 7. KV Transfer: NIXL / Mooncake / 전체 Config

### 7.1 공통 입력 구조

```yaml
pdCellSpec:
  models:
    - name: example-pd
      kvTransfer:
        connector: NixlConnector
        config: {}
        prefillConfig: {}
        decodeConfig: {}
```

| field | 동작 |
|---|---|
| `connector` | `kv_connector`로 변환. `NixlConnector`, `MooncakeConnector` 외에도 image에 등록된 connector 또는 외부 connector 사용 가능 |
| `config` | 모든 P/D engine에 공통 적용되는 raw `KVTransferConfig` map |
| `prefillConfig` | Prefill에만 적용하며 `config`보다 우선 |
| `decodeConfig` | Decode에만 적용하며 `config`보다 우선 |
| `prefill.kvTransferConfig` | 특정 모델의 Prefill phase 최종 override |
| `decode.kvTransferConfig` | 특정 모델의 Decode phase 최종 override |

`config` 계열은 vLLM Python field 이름과 같은 **snake_case**를 그대로 쓴다. Helm이 임의로 connector option을 제한하지 않고 JSON으로 전달하므로 vLLM 0.27.1의 현재 field와 향후 connector-specific field를 사용할 수 있다.

`kv_connector`와 `kv_role`은 사용자가 `config`에 넣어도 Helm이 마지막에 다음 값으로 강제한다.

```text
Prefill → kv_connector=<connector>, kv_role=kv_producer
Decode  → kv_connector=<connector>, kv_role=kv_consumer
```

이는 P/D role을 잘못 지정해 Cell이 반대로 동작하는 것을 방지하기 위한 contract다.

### 7.2 vLLM 0.27.1 `KVTransferConfig` field

| `config` key | vLLM 기본값 | 사용 의미 / 주의점 |
|---|---:|---|
| `engine_id` | 자동 UUID | 직접 고정하면 replica/engine 간 ID 충돌 위험이 있으므로 일반적으로 생략 |
| `kv_buffer_device` | 현재 platform device | `cuda`, `cpu`, `xpu`; NIXL host buffer 등 connector가 요구할 때 지정 |
| `kv_buffer_size` | `1e9` | 주로 TorchDistributedConnector buffer byte 크기 |
| `kv_rank` | `null` | rank 기반 connector용. vLLM 설명상 이 방식은 현재 1P1D 제약이 있으므로 NIXL/Mooncake에 불필요하게 지정하지 않음 |
| `kv_parallel_size` | `1` | rank 기반 KV transfer parallel instance 수 |
| `kv_ip` | `127.0.0.1` | connector가 이 공통 endpoint field를 사용할 때만 지정 |
| `kv_port` | `14579` | connector가 이 공통 port field를 사용할 때만 지정 |
| `kv_connector_extra_config` | `{}` | connector-specific option 전체 |
| `kv_connector_module_path` | `null` | V1 외부 connector Python module path |
| `enable_permute_local_kv` | `false` | NIXL HND↔NHD layout permute 실험 옵션 |
| `kv_load_failure_policy` | `fail` | `fail` 또는 `recompute` |

정확한 기준은 [vLLM v0.27.1 `KVTransferConfig` source](https://github.com/vllm-project/vllm/blob/v0.27.1/vllm/config/kv_transfer.py)다.

### 7.3 NixlConnector

최소값:

```yaml
pdCellSpec:
  models:
    - name: example-pd
      kvTransfer:
        connector: NixlConnector
```

명시적 운영 예:

```yaml
pdCellSpec:
  models:
    - name: example-pd
      kvTransfer:
        connector: NixlConnector
        config:
          kv_buffer_device: cuda
          kv_load_failure_policy: fail
          kv_connector_extra_config:
            backends:
              - UCX
            enforce_handshake_compat: true
            # enable_cross_layers_blocks: "True"
```

주요 NIXL extra option:

| key | 의미 |
|---|---|
| `backends` | NIXL plugin 목록. 기본은 UCX이며 build에 따라 UCX/GDS/LIBFABRIC 등을 선택 |
| `enforce_handshake_compat` | P/D model·dtype·attention·KV layout 호환성 검사. 안전상 `false`로 끄지 않음 |
| `enable_cross_layers_blocks` | 지원 attention backend에서 cross-layer contiguous block transfer 활성화 |
| `bidirectional_kv_xfer` | multi-turn 등의 양방향 KV 전송 기능을 실제로 사용할 때만 활성화 |

P와 D는 최소한 vLLM/NIXL connector 버전, model architecture, dtype, attention backend, KV cache dtype이 맞아야 한다. TP와 block size는 모델/feature 제약 안에서 다를 수 있다. 자세한 호환성은 [vLLM NixlConnector guide](https://docs.vllm.ai/en/v0.27.1/features/nixl_connector_usage/)와 [compatibility matrix](https://docs.vllm.ai/en/v0.27.1/features/nixl_connector_compatibility/)를 따른다.

NIXL의 UCX 전송은 NCCL 설정을 재사용하지 않는다. `UCX_TLS`, `UCX_NET_DEVICES` 같은 UCX 환경변수는 실제 Network A/B의 NIC·transport 검증 결과에 맞춰 `pdCellSpec.env` 또는 모델 env로 선언한다.

### 7.4 MooncakeConnector

Network B처럼 RDMA를 쓰지 않는 검증 예:

```yaml
pdCellSpec:
  models:
    - name: example-pd
      kvTransfer:
        connector: MooncakeConnector
        config:
          kv_load_failure_policy: fail
          kv_connector_extra_config:
            mooncake_protocol: tcp
            num_workers: 16
        bootstrapPortBase: 9001
        abortRequestTimeout: 600
```

vLLM 0.27.1 자체 기본은 `mooncake_protocol=rdma`, `num_workers=10`이다. 그러므로 TCP를 의도하면 반드시 `config.kv_connector_extra_config.mooncake_protocol: tcp`를 적는다.

Prefill/Decode에 렌더되는 핵심 JSON:

```json
{
  "kv_connector": "MooncakeConnector",
  "kv_role": "kv_producer | kv_consumer",
  "kv_load_failure_policy": "fail",
  "kv_connector_extra_config": {
    "mooncake_protocol": "tcp",
    "num_workers": 16
  }
}
```

Mooncake 전용 환경변수는 connector를 선택했을 때만 자동 생성한다.

```text
Prefill bootstrap: VLLM_MOONCAKE_BOOTSTRAP_PORT=9001+index
P/D timeout:       VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=600
```

### 7.5 `kv_load_failure_policy` 운영 권장

이 설정은 standard P→D flow에서 **Decode가 Prefill의 KV block을 load하지 못했을 때**의 처리 정책이다. [vLLM 0.27.1 NIXL guide](https://github.com/vllm-project/vllm/blob/v0.27.1/docs/features/nixl_connector_usage.md#kv-load-failure-policy)는 production에서 `recompute`가 Decode engine에 Prefill 연산을 유입시켜 진행 중인 Decode의 latency까지 악화할 수 있다고 경고한다.

| Engine | 권장값 | 근거 |
|---|---|---|
| Prefill (`kv_producer`) | `fail` | 표준 단방향 P→D에서는 remote KV를 load하지 않아 이 정책이 실질적으로 발동하지 않는다. 양방향 KV transfer를 별도로 활성화하지 않는 한 기본값을 유지한다. |
| Decode (`kv_consumer`) | `fail` | KV load 실패 요청을 즉시 실패시켜 Decode latency isolation을 보존한다. 특히 장기 context에서는 Decode 측 recompute가 큰 Prefill 연산으로 변해 tail latency를 악화시킨다. |

따라서 기본 운영값은 phase override 없이 model 공통으로 한 번 선언한다.

```yaml
kvTransfer:
  connector: NixlConnector
  config:
    kv_load_failure_policy: fail
```

`recompute`는 다음 조건을 모두 검토한 임시 availability-first 정책으로만 사용한다.

- request 실패보다 단일 요청의 지연 증가를 우선 허용
- 입력 길이가 짧고 상한이 통제됨
- Decode GPU에 Prefill fallback 여유가 있음
- KV transfer 실패율과 Decode ITL/p99에 alert가 있음

현재 운영 workload처럼 50K~200K 장기 context 비중이 높으면 `recompute`를 기본값으로 사용하지 않는다. 초기 connector 검증에서도 `fail`을 사용해야 transfer 실패가 Decode의 local recompute에 가려지지 않는다.

### 7.6 Hardware / fabric별 transport 선택

Transport는 GPU 제품명만으로 결정할 수 없다. 같은 H200이라도 Network A에는 IB/GDRDMA가 있고 Network B에는 없으며, 현재 P/D Cell은 P와 D가 한 Pod·한 Node에 있어 IB 자체를 사용하지 않는다. Helm render 시점에는 실제 배치 Node, UCX/Mooncake build plugin, PCIe peer-access 상태를 알 수 없으므로 chart가 `H200 → RDMA`처럼 자동 선택하면 잘못된 경로를 강제할 수 있다.

| 배치 범위 / 장비 | NIXL 권장 | Mooncake 권장 | 운영 판단 |
|---|---|---|---|
| 같은 Node, L40S/H100, NVLink 없음 | `backends: [UCX]`; `UCX_TLS` 생략 또는 `all` | 비교 검증 시 `mooncake_protocol: tcp` | NIXL/UCX 우선. NVLink가 없어도 network TCP가 아니라 가능한 CUDA IPC/P2P·PCIe local path를 사용한다. |
| 같은 Node, H200 NVLink/NVSwitch | `backends: [UCX]`; `UCX_TLS` 생략 또는 `all` | 별도 성능 검증 전에는 NIXL 우선 | UCX의 CUDA transport가 topology가 제공하는 local GPU P2P path를 사용하도록 제한하지 않는다. Cell 내부에서는 IB HCA를 강제하지 않는다. |
| 서로 다른 Node, IB + GDRDMA 검증 완료 | `backends: [UCX]`; 시작은 `UCX_TLS=all`, 이후 `rc,cuda`와 검증된 HCA pin 비교 | `mooncake_protocol: rdma` | Network A 장기 multi-node P/D 후보. `UCX_NET_DEVICES`/`device_name`은 실제 NIC 이름 확인 후 지정한다. |
| 서로 다른 Node, RDMA 없음 | NIXL/UCX의 `tcp,cuda` 또는 Mooncake TCP와 비교 | `mooncake_protocol: tcp` | 대용량 장기-context KV에는 병목 가능성이 높으므로 기능 지원과 성능을 별도 검증한다. |

NIXL의 기본 plugin은 UCX다. 같은 Node에서 NVLink 유무는 `backends` 이름을 바꾸는 조건이 아니다. NVLink/NVSwitch 또는 PCIe는 CUDA peer path 아래의 물리 fabric이고, NIXL 설정에는 계속 `UCX`를 사용한다.

안전한 초기값:

```yaml
env:
  - name: UCX_TLS
    value: all
  - name: UCX_NET_DEVICES
    value: all
kvTransfer:
  connector: NixlConnector
  config:
    kv_load_failure_policy: fail
    kv_connector_extra_config:
      backends: [UCX]
      enforce_handshake_compat: true
```

`UCX_TLS`를 직접 제한할 때는 GPU memory transport를 반드시 포함한다. 예를 들어 cross-node IB는 `rc,cuda`, TCP는 `tcp,cuda`부터 검증한다. NCCL의 `NCCL_IB_HCA`, `NCCL_SOCKET_IFNAME`은 NIXL/UCX transport 선택에 적용되지 않는다.

Mooncake는 vLLM 0.27.1에서 protocol 기본값이 `rdma`이므로 RDMA가 없는 Node에서는 반드시 `tcp`를 명시한다. chart가 protocol을 자동 결정하지 않는다.

배포 전/후 확인 항목:

```text
nvidia-smi topo -m                 # GPU 간 NVLink/PCIe 및 NIC NUMA 관계
CUDA p2pBandwidthLatencyTest       # GPU peer access와 실제 local bandwidth
ucx_info -d                        # cuda/IB/TCP transport가 image에 포함됐는지
ibv_devinfo                        # cross-node RDMA를 사용할 때 HCA/port 상태
vLLM NIXL transfer metrics/log     # 실제 성공/실패, bandwidth, expired request
```

### 7.7 같은 Pod 안의 port 충돌 방지

P2D2처럼 여러 vLLM server가 한 Pod network namespace를 공유하면 HTTP port뿐 아니라 vLLM internal/DP/NIXL side-channel port도 고유해야 한다. Template이 다음 값을 자동 할당한다.

| 목적 | Prefill 기본값 | Decode 기본값 | override field |
|---|---:|---:|---|
| HTTP | `8101+index` | `8201+index` | `portBase` |
| vLLM internal | `20000 + 100×index` | `30000 + 100×index` | `internalPortMode: vllm`, `internalPortBase`, `internalPortStride` |
| DP master | `24000+index` | `34000+index` | `internalPortMode: dp`, `dpMasterPortBase` |
| NIXL side channel | `5600+index` | `5700+index` | `sideChannelPortBase` |
| Mooncake bootstrap | `9001+index` | 해당 없음 | `models[].kvTransfer.bootstrapPortBase` |

`internalPortMode`는 phase profile의 parallelism에 맞춘다.

| mode | 자동 env | 사용 시점 |
|---|---|---|
| `vllm` | `VLLM_PORT` | 기본값. TP/PP 등 non-DP engine |
| `dp` | `VLLM_DP_MASTER_PORT` | profile이 vLLM data parallel engine을 구성할 때 |
| `auto` | 둘 다 주입하지 않음 | vLLM의 동적 port 선택에 맡길 때 |

vLLM 공식 NIXL integration도 non-DP에는 `VLLM_PORT`, DP에는 `VLLM_DP_MASTER_PORT`를 구분한다. 두 값을 동시에 강제하지 않는다. 자동 생성 env는 phase의 사용자 env보다 우선하므로 포트를 변경할 때는 env를 직접 덮기보다 위 field를 사용한다.

NIXL side-channel env는 `NixlConnector`, `NixlPullConnector`, `NixlPushConnector`일 때만 자동 생성한다. `MultiConnector`의 child로 NIXL을 넣는 경우에는 `models[].kvTransfer.nixlSideChannelEnabled: true`를 명시한다.

### 7.8 지원 범위의 경계

Chart는 raw `KVTransferConfig`를 전달하므로 `MultiConnector`, external connector 등도 표현할 수 있다. 다만 다음은 Helm이 보장하지 않는다.

- 선택한 image에 connector 및 native library가 실제 포함되어 있는지
- connector가 `disaggregated_prefill_orchestrated`의 `kv_transfer_params` flow를 지원하는지
- connector/model/attention backend/TP layout 조합이 호환되는지
- `MultiConnector` 내부 child connector가 요구하는 별도 env/bootstrap lifecycle

현재 이 PR의 runtime acceptance target은 `NixlConnector`와 `MooncakeConnector` 두 가지다.

---

## 8. Service / LiteLLM / Global Router 연결

P/D Cell Service는 **Router만 노출**한다.

```text
Service/<release>-<name>-engine-service
        │
        └─ Pod :8000 / pd-router
```

P/D engine port는 ClusterIP Service로 노출하지 않는다.

따라서 LiteLLM은 기존 모델별 Service endpoint 패턴을 유지할 수 있다.

```text
LiteLLM
  ↓
<release>-<name>-engine-service
  ↓
Cell Router replica
  ↓
Prefill → Decode
```

Cell replica를 늘려도 LiteLLM config는 변경하지 않는다.

### Global Router discovery

PD Cell Pod에는 기존 `servingEngineSpec.labels`가 그대로 붙는다.

Global LMRouter 0.1.8은 기존과 같은 namespace/label selector로 Pod를 찾고 `:8000`을 호출한다.

PD Cell Pod의 `:8000`은 Cell Router이므로 Global Router는 Cell을 일반 serving endpoint처럼 볼 수 있다.

```text
Global Router
  ├─ 기존 integrated vLLM Pod :8000
  └─ PD Cell Pod             :8000 → Cell Router
```

Global Router는 Cell 내부 P/D topology를 알 필요가 없다.

---

## 9. Prometheus Metrics

Prefill/Decode에는 `PROMETHEUS_MULTIPROC_DIR=/tmp`를 Helm이 마지막에 강제한다. 사용자 env에 다른 값이 있어도 `/tmp`가 우선한다.

vLLM engine metrics endpoint는 `/metrics`다.

Cell 예:

```text
PodIP:8101/metrics → prefill-0
PodIP:8102/metrics → prefill-1
PodIP:8201/metrics → decode-0
PodIP:8202/metrics → decode-1
```

현재 `8000`만 keep하는 기존 job은 일반 vLLM/Router용으로 남겨두고 P/D engine 전용 job을 추가하는 것을 권장한다.

예시:

```yaml
- job_name: kubernetes-vllm-pd-engine
  metrics_path: /metrics

  kubernetes_sd_configs:
    - role: pod
      namespaces:
        names:
          - inference

  relabel_configs:
    - source_labels: [__meta_kubernetes_pod_label_pd_cell]
      regex: 'true'
      action: keep

    - source_labels: [__meta_kubernetes_pod_container_name]
      regex: '(prefill-[0-9]+|decode-[0-9]+)'
      action: keep

    - source_labels: [__meta_kubernetes_pod_phase]
      regex: Running
      action: keep

    - source_labels: [__meta_kubernetes_pod_name]
      target_label: pd_cell

    - source_labels: [__meta_kubernetes_pod_label_pd_deployment]
      target_label: pd_deployment

    - source_labels: [__meta_kubernetes_pod_node_name]
      target_label: node

    - source_labels: [__meta_kubernetes_pod_container_name]
      target_label: container

    - source_labels: [__meta_kubernetes_pod_container_name]
      regex: 'prefill-.*'
      replacement: prefill
      target_label: pd_role

    - source_labels: [__meta_kubernetes_pod_container_name]
      regex: 'decode-.*'
      replacement: decode
      target_label: pd_role

    - source_labels:
        - __meta_kubernetes_pod_name
        - __meta_kubernetes_pod_container_name
      separator: '/'
      target_label: instance
```

### 왜 `instance=PodIP`만 쓰면 안 되는가

PD Cell은 하나의 Pod 안에 여러 vLLM process가 있다.

```text
10.0.0.10:8101
10.0.0.10:8102
10.0.0.10:8201
10.0.0.10:8202
```

port를 제거해 모두 `instance=10.0.0.10`으로 만들면 engine identity가 사라진다.

P/D Cell에서는 `pod/container` 또는 `PodIP:port`를 instance로 유지한다.

---

## 10. 장애 정책

단기 첫 버전은 strict Cell readiness다.

```text
pd-router READY
AND prefill-0..N READY
AND decode-0..M READY
→ Pod READY
→ Service endpoint 포함
```

예: P2:D2에서 P0 crash

```text
P0 crash
  ↓
Pod NotReady
  ↓
해당 Cell이 Service endpoint에서 제거
  ↓
다른 Cell replica가 신규 요청 처리
  ↓
Kubelet이 P0 restart
  ↓
model / Mooncake 초기화
  ↓
P0 Ready
  ↓
Pod Ready
  ↓
Cell 자동 재가입
```

이 방식은 Cell 내부 degraded serving보다 단순하지만 장애 의미가 명확하다.

장기에는 다음을 별도 구현한다.

```text
minReadyPrefill >= 1
AND minReadyDecode >= 1
→ degraded Cell serving
```

---

## 11. Deployment 전략

Cell 하나가 Node GPU 전체를 점유할 수 있으므로 기본 RollingUpdate는 다음으로 설정한다.

```yaml
maxSurge: 0
maxUnavailable: 1
```

이유:

`maxSurge: 1`이면 기존 Cell이 GPU를 점유한 상태에서 신규 Cell 하나를 추가로 스케줄해야 하므로 spare GPU Node가 없으면 rollout이 Pending될 수 있다.

모델별 `strategy`로 override 가능하다.

---

## 12. 개발/테스트 순서

### 12.1 Static

```bash
helm lint ./helm
helm template <release> ./helm \
  -f <existing-global-values> \
  -f ./helm/examples/pd-cell-values.yaml
```

확인:

- 기존 integrated model manifest 변화 없음
- P/D Cell Deployment/Service만 추가
- container 수량
- port
- GPU requests
- CPU/memory inherited defaults
- static router backend list
- NIXL/Mooncake connector와 producer/consumer role

### 12.2 P1:D1

최초 runtime 검증은 P1:D1로 시작한다.

확인:

- Router health
- P health
- D health
- `/v1/models`
- `/metrics`
- non-streaming
- streaming
- long-context request
- 선택한 connector의 handshake/transfer log

### 12.3 P2:D2 / P3:D1

`count` 값만 변경해 topology가 자동 생성되는지 확인한다.

같은 `servedModelNames`로 P1D1/P2D1/P2D2/P1D3를 동시에 선언할 수 있다. topology별 결과를 분리할 때는 각 `<release>-<name>-engine-service`를 직접 호출한다.

### 12.4 Replica scale

```bash
kubectl scale deployment <release>-<name>-pd-cell --replicas=2
```

또는 values의 `replicaCount`를 변경한다.

확인:

- Node 이름을 지정하지 않아도 scheduler가 배치
- GPU 8개 Cell은 8 GPU가 가용한 Node에 원자적으로 배치
- Service endpoint 증가
- LiteLLM config 변화 없음

### 12.5 Failure

각각 테스트한다.

```text
prefill container kill
decode container kill
router container kill
Pod delete
Node drain
```

Cell이 제거되고 복구 후 자동 재가입하는지 확인한다.

---

## 13. 현재 초안에서 의도적으로 하지 않는 것

- P와 D 독립 Deployment
- P와 D 독립 autoscaling
- Cross-node P/D KV transfer
- degraded Cell serving
- multi-node engine
- Fabric P/D
- Native MP/LWS
- cache-aware P/D routing

이들은 0.1.12+ 범용 Disaggregated Serving 트랙에서 구현한다.

---

## 14. 장기 흡수 포인트

단기 `pdCellSpec.models[]`에서 장기적으로 가져갈 field:

```text
name / servedModelNames
prefill/decode topology
profile
resource contract
KV connector
Router contract
metrics identity
failure semantics
```

장기적으로 packaging만 바뀐다.

```text
0.1.8
pdCellSpec.models[]

          ↓

0.1.12+
modelSpec
  deploymentMode: disaggregated
  disaggregatedServing:
    topology: nodeLocal | fabric
```

---

## 15. 구현 파일

```text
helm/templates/deployment-pd-cell.yaml
  → Cell Pod / Router / P / D 생성

helm/templates/service-pd-cell.yaml
  → Cell Router를 모델 Service로 노출

helm/examples/pd-cell-values.yaml
  → values 예제

helm/tests/pdCell_test.yaml
  → P2:D2, P3:D1, replica 0, 동일 servedModelNames, disabled renderer 테스트

helm/docs/PD_CELL_0.1.8_KO.md
  → 본 문서
```

---

## 핵심 요약

```text
한 모델
  ↓
pdCellSpec.models[] 한 block
  ↓
Deployment replica = Cell count
  ↓
한 Pod 안에 Router + P×N + D×M
  ↓
Pod GPU request 합계로 Kubernetes 자동 scheduling
  ↓
Service는 Router :8000만 노출
  ↓
Cell Router가 localhost P/D를 orchestration
  ↓
Global Router는 Cell 자체만 discover
```

운영 values는 `name / servedModelNames / image / topology / GPU / profile / kvTransfer` 중심으로 유지하고, 공통 Router·스케줄링 정책은 `pdCellSpec` 최상위에 한 번만 둔다. KV connector와 config는 각 `models[]`가 소유한다. 전체 속성은 `PD_CELL_VALUES_REFERENCE_KO.md`, 복사용 전체 예제는 `pd-cell-values-full.yaml`을 기준으로 한다.

단기 목표는 이 구조를 **기존 0.1.8 운영 경로에 영향 없이 실제로 검증하는 것**이다.
