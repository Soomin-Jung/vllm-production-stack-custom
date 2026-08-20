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

예를 들어 values에서 다음처럼 선언한다.

```yaml
pdCellSpec:
  enabled: true
  models:
    - name: example-pd-p2d2
      servedModelName: example-model-pd-p2d2
      replicaCount: 3

      prefill:
        count: 2
        requestGPU: 2

      decode:
        count: 2
        requestGPU: 2
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
│ Mooncake bootstrap                    │
│ prefill-0                  :9001     │
│ prefill-1                  :9002     │
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

따라서 `replicaCount: 3`이면 Cell 세 개를 scheduler가 서로 가용한 Node에 배치한다.

기본 운영에서는 `nodeName`을 지정하지 않는다.

---

## 3. Values와 Template 연결

### 3.1 최상위

```yaml
pdCellSpec:
  enabled: true
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

---

### 3.2 모델 identity

```yaml
- name: example-pd-p2d2
  servedModelName: example-model-pd-p2d2
  replicaCount: 2
```

의미:

| field | 의미 |
|---|---|
| `name` | Kubernetes 리소스 identity / Cell deployment name |
| `servedModelName` | P/D Router 및 vLLM API에서 사용하는 model ID |
| `replicaCount` | P/D Cell 개수 |

같은 weight를 다른 topology로 동시에 시험하려면 `name`과 `servedModelName`을 분리한다.

예:

```text
example-pd-p3d1 → example-model-pd-p3d1
example-pd-p2d2 → example-model-pd-p2d2
```

---

### 3.3 vLLM image

```yaml
repository: registry.example/vllm
tag: v0.26.0-mooncake
```

Prefill/Decode 모든 engine container가 같은 image를 사용한다.

MooncakeConnector를 사용할 경우 해당 image에 필요한 Mooncake runtime/package가 포함되어 있어야 한다.

---

### 3.4 Cell Router

```yaml
router:
  repository: registry.example/lmstack-router
  tag: validated-0.1.12
  port: 8000
  healthCheckInterval: 30
  healthCheckTimeout: 5
```

Cell Router는 기존 Global Router와 별도 process다.

```text
Global LMRouter 0.1.8
  └─ Cell Router endpoint :8000
       ├─ Prefill pool
       └─ Decode pool
```

Cell Router image는 반드시 다음 기능이 검증된 image를 pin한다.

```text
disaggregated_prefill_orchestrated
static service discovery
static model labels
static backend health check
```

`latest` 사용은 권장하지 않는다.

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
--static-models <servedModelName repeated>
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
  --kv-transfer-config <Mooncake producer config>
```

Decode engine 실행:

```text
vllm serve
  --host 0.0.0.0
  --port 8201
  --config /profiles/example/pd-decode.yaml
  --kv-transfer-config <Mooncake consumer config>
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
- Mooncake bootstrap port
- Router membership
- Kubernetes scheduling

`servedModelName`과 profile의 `served-model-name`은 반드시 같은 값이 되도록 운영한다.

---

## 6. Global env / volume inheritance

현재 custom 0.1.8 baseline과 동일하게 `global.env`, `global.extraVolumes`, `global.extraVolumeMounts`를 P/D engine에 상속한다.

따라서 기존 `/profiles` mount나 공통 cache mount를 그대로 재사용할 수 있다.

우선순위:

```text
global env
   ↓
model env
   ↓
phase(prefill/decode) env
   ↓
runtime-required env
```

volume/mount는 이름 기준으로 model-level 값이 global 값을 덮어쓴다.

---

## 7. Mooncake 연결

예:

```yaml
kvTransfer:
  connector: MooncakeConnector
  protocol: tcp
  numWorkers: 16
  bootstrapPortBase: 9001
  abortRequestTimeout: 600
```

Prefill에는 producer config가 주입된다.

```json
{
  "kv_connector": "MooncakeConnector",
  "kv_role": "kv_producer",
  "kv_connector_extra_config": {
    "mooncake_protocol": "tcp",
    "num_workers": 16
  }
}
```

Decode에는 consumer config가 주입된다.

```json
{
  "kv_connector": "MooncakeConnector",
  "kv_role": "kv_consumer",
  "kv_connector_extra_config": {
    "mooncake_protocol": "tcp",
    "num_workers": 16
  }
}
```

같은 Pod는 network namespace를 공유하므로 localhost endpoint를 사용할 수 있다.

Prefill producer가 여러 개면 bootstrap port는 같은 network namespace에서 충돌하지 않도록 순차 할당한다.

```text
prefill-0 → 9001
prefill-1 → 9002
prefill-2 → 9003
```

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
- static router backend list
- Mooncake role

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
- Mooncake transfer log

### 12.3 P2:D2 / P3:D1

`count` 값만 변경해 topology가 자동 생성되는지 확인한다.

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
name / servedModelName
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
  → P2:D2, P3:D1, disabled renderer 테스트

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

단기 목표는 이 구조를 **기존 0.1.8 운영 경로에 영향 없이 실제로 검증하는 것**이다.
