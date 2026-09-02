# Agentic API 경로 및 Router 통합 계약

업데이트: 2026-09-02  
대상: Agentic API v0.5.0, LMStack Router 0.1.9 normal route, 선택적 P/D Cell

## 1. 채택 구조

외부 API path가 protocol lane을 결정하고 `model`은 model identity만 표현한다.

```text
Client
  -> authenticated edge / vLLM Proxy
       /v1/completions, /v1/chat/completions
         -> LiteLLM hosted_vllm lane

       /v1/messages
         -> LiteLLM Anthropic lane

       /v1/responses prefix
         -> Agentic API
              <-> PostgreSQL
              -> reconstructed/stateless POST /v1/responses
              -> LMStack Router normal route
              -> integrated vLLM or validated P/D Cell
```

이 구조의 책임 경계는 다음과 같다.

| 계층 | 소유 책임 | 소유하지 않는 책임 |
| --- | --- | --- |
| edge / vLLM Proxy | method/path/Upgrade 기반 lane 선택, 인증, transport policy | Responses state 재구성 |
| LiteLLM | 기존 Completions/Chat/Messages provider compatibility | Responses durable state |
| Agentic API | Responses typed item, continuation, conversation, compaction, client WebSocket | model별 backend registry |
| global LMStack Router | `model` 기반 backend 선택과 normal HTTP/SSE forwarding | durable state, P/D KV protocol |
| cell-local P/D Router | Prefill/Decode 선택과 KV connector metadata | public conversation state |

## 2. edge route table

`/v1/responses`는 하나의 POST endpoint만 의미하지 않는다. path prefix 전체와 method/Upgrade를 같은 Agentic API
서비스로 보낸다.

| match | upstream | 핵심 policy |
| --- | --- | --- |
| `POST /v1/completions` | LiteLLM hosted_vllm | 기존 inference retry/SSE policy |
| `POST /v1/chat/completions` | LiteLLM hosted_vllm | 기존 inference retry/SSE policy |
| `POST /v1/messages` | LiteLLM Anthropic wrapper | Anthropic header, block, SSE, error fidelity |
| `POST /v1/messages/count_tokens` | LiteLLM Anthropic wrapper, 사용하는 경우 | non-stream JSON |
| `POST /v1/responses` | Agentic API | JSON/SSE, buffering off, blind replay off |
| `GET /v1/responses` + WebSocket Upgrade | Agentic API | Upgrade와 long-lived connection 전달 |
| `/v1/responses/*` | Agentic API | compact/retrieve/cancel/delete/input-items 중 사용 surface |
| `/v1/conversations*` | Agentic API, 사용하는 경우 | conversation lifecycle와 auth scope |

`/v1/models`의 기존 공개 소유자는 즉시 바꿀 필요가 없다. 다만 edge와 Agentic upstream에서 같은 model name이
조회되고 요청되는지 contract test로 고정한다.

### routing pseudocode

```text
if path starts with /v1/responses:
    proxy_to(agentic_internal)
else if path starts with /v1/conversations:
    proxy_to(agentic_internal)
else if path is /v1/messages or /v1/messages/count_tokens:
    proxy_to(litellm_anthropic)
else if path is /v1/completions or /v1/chat/completions:
    proxy_to(litellm_hosted_vllm)
else:
    apply_existing_route_or_reject
```

public edge의 `/v1/responses` upstream과 Agentic의 `LLM_API_BASE`는 서로 다른 logical endpoint여야 한다.
Agentic downstream을 public edge의 같은 `/v1/responses`로 설정하면 recursion이 생긴다.

## 3. Agentic API upstream 계약

Agentic API v0.5.0 standalone server는 `LLM_API_BASE` 하나를 받는다. model별 base URL map을 자체적으로 관리하지
않으므로 여러 model을 제공할 때 이 값은 multi-model logical upstream을 가리켜야 한다.

```text
LLM_API_BASE=http://internal-lmstack-router:9400
```

단일 base URL은 단일 physical backend를 뜻하지 않는다. global Router가 model을 기준으로 여러 vLLM Service,
Pod 또는 P/D Cell을 선택할 수 있다.

Agentic은 요청 조건에 따라 두 path를 모두 사용한다.

- stateless fast path: client body에 가까운 request를 upstream으로 proxy
- stateful executor path: PostgreSQL의 typed item과 continuation state를 resolve한 뒤 upstream request 생성

따라서 downstream은 `previous_response_id`를 저장하거나 해석하는 state runtime이 아니라, 두 종류의 유효한
Responses request를 처리하는 stateless inference plane으로 취급한다.

## 4. LMStack Router 0.1.9 판정

공식 `vllm-stack-0.1.9` source commit은
[`20a6580`](https://github.com/vllm-project/production-stack/tree/20a658044af0dea70e9e0136494bb9979cfd9fab)이다.
[`/v1/responses` PR #691](https://github.com/vllm-project/production-stack/pull/691)은 Responses 전용
translator가 아니라 generic request route를 연결한다.

### 가능한 것

- `POST /v1/responses`에서 `model`에 맞는 backend 선택
- selected backend의 같은 path로 request 전달
- backend HTTP body/SSE byte stream 전달
- Agentic이 이미 resolve한 state를 다시 provider schema로 변환하지 않는 thin routing

### 정확한 한계

“body를 전혀 검증하지 않는다”는 표현은 사용하지 않는다. 0.1.9 normal path도 다음을 수행한다.

1. body를 JSON으로 parse
2. `model` field 확인
3. optional pre-request callback 실행
4. optional request rewriter 실행
5. alias 사용 시 `model` 변경과 JSON reserialize
6. routing logic에 필요한 field inspection

다음 조건에서만 Responses payload가 사실상 opaque하게 전달된다.

```text
routing logic     = round-robin
request rewriter  = off
model alias       = off where possible
custom callback   = semantic mutation 없음
P/D legacy branch = 사용하지 않음
```

0.1.9 Router는 downstream Responses WebSocket server일 필요가 없다. WebSocket은 client와 Agentic API 사이에서
종료되고 Agentic은 inference plane에 HTTP `POST /v1/responses`를 사용한다.

### 초기 rollout 설정

0.1.9 KV-aware Router는 `prompt` 중심으로 tokenization하며 Responses의 typed `input`을 해석하지 않는다. 초기
Responses lane에서는 round-robin을 사용한다. conversation affinity나 Responses-aware KV routing은 별도 검증 뒤
도입한다.

0.1.9 legacy disaggregated-prefill branch도 사용하지 않는다. 해당 path는 Chat/Completions의 `max_tokens` 중심이며
Responses의 `max_output_tokens` P/D contract가 아니다.

## 5. P/D Cell 경계

Agentic rehydration은 conversation state 문제를 해결하지만 P/D orchestration 문제를 해결하지 않는다.

```text
Agentic
  -> global LMStack Router normal route
  -> selected P/D Cell
  -> cell-local Responses-aware P/D Router
  -> Prefill / Decode
```

global Router는 model/Cell 선택만 하고 cell-local Router가 다음을 소유한다.

- Prefill request copy의 `max_output_tokens=1`
- connector별 `kv_transfer_params`
- original Decode request 보존
- Responses status, error, SSE와 cancellation 전파

`vllm-project/router` v0.1.15 tag는 source commit
[`1fbcde7`](https://github.com/vllm-project/router/tree/1fbcde7443d75b36befb61bc081f64c2a1f13a4b)이며,
`/v1/responses` Prefill copy에 `max_output_tokens=1`을 설정하고 `max_tokens`를 주입하지 않는 코드와 단위 테스트를
포함한다. 내부 image는 tag뿐 아니라 이 source commit에 대응하는 digest로 pin하고 Mooncake/NIXL data path를 E2E로
검증한다.

## 6. Kubernetes Service가 추가하는 선택

L7 Router가 multi-replica ClusterIP Service를 호출하면 최종 replica는 Router가 아니라 Kubernetes L4 dataplane이
선택한다.

```text
L7 Router: model-a Service 선택
Kubernetes: new TCP flow를 EndpointSlice의 Pod 하나로 DNAT
```

model-level routing은 여전히 유효하지만 exact Pod의 KV locality를 보고 선택하는 것은 불가능하다. keep-alive, HTTP/2,
SSE, WebSocket 연결은 conntrack과 connection pool 때문에 선택된 Pod에 오래 붙을 수 있다.

Pod-level KV-aware routing이 필요하면 Router가 EndpointSlice/Pod IP를 발견하고 selected Pod IP로 직접 연결하며,
Pod별 connection pool과 drain/eviction을 소유해야 한다. Service VIP를 backend URL로 유지한 채 KV-aware score만 추가하는
것은 정확한 replica routing이 아니다.

P/D Cell Service 뒤에 replica가 하나뿐이거나 cell-local Router까지가 하나의 failure/locality domain이라면 Service
호출 자체는 문제되지 않는다. 다중 Cell에서 exact cell affinity가 필요하면 global Router의 Pod-IP discovery 여부를
별도로 확인한다.

## 7. HTTP/SSE/WebSocket transport policy

### HTTP JSON

- request method, path, query, body와 API 의미가 있는 header를 보존
- hop-by-hop header는 proxy가 재생성
- upstream status와 error body를 임의의 200/공통 error로 변환하지 않음
- request body limit와 header limit를 endpoint별로 명시

### SSE

- proxy buffering과 response compression을 초기 baseline에서 끔
- event 하나와 TCP chunk 하나가 일치한다고 가정하지 않음
- 첫 byte가 아니라 첫 완전한 SSE event까지의 latency를 관측
- 충분한 idle timeout과 rolling-drain 시간을 설정
- partial SSE 이후 전체 POST를 자동 replay하지 않음

### WebSocket

- `GET /v1/responses`의 `Upgrade`, `Connection`, subprotocol을 Agentic API까지 전달
- close code/reason, ping/pong, half-open과 reconnect를 검증
- connection lifetime이 Pod drain 한도보다 길 수 있음을 고려
- LMStack Router에는 WebSocket을 전달하지 않음

## 8. retry와 side effect

Responses agent flow는 model output 중 tool execution을 일으킬 수 있다. client connection이 끊겼다는 이유만으로 edge가
원본 POST를 replay하면 filesystem, shell, 외부 API 같은 side effect가 중복될 수 있다.

```text
response header 전 connect failure
  -> 제한적 retry 후보

upstream accept 여부 불명 / partial SSE / tool call 이후
  -> blind replay 금지
  -> 저장된 response 상태 조회 또는 명시적 recovery contract 사용
```

Completions lane과 Responses lane은 retry budget과 replay 조건을 분리한다. load balancer의 generic retry 기본값을
Responses POST에 그대로 적용하지 않는다.

## 9. image provenance

fixed tag는 `latest`보다 낫지만 immutable proof는 아니다. 운영 기록에는 다음을 함께 보관한다.

```text
image repository + tag
runtime image digest
source repository + commit SHA
build recipe and dependency lock
SBOM/provenance where available
contract-test result
```

특히 `0.1.9-dev` 같은 내부 tag는 official tag와 같은 source임을 이름만으로 추론하지 않는다. running Pod의
`imageID`, Router `/version`, source lock을 서로 대조한다.

## 10. release gate

### protocol correctness

- Completions/Chat/Messages가 기존 LiteLLM lane을 그대로 사용
- Responses non-stream JSON과 SSE terminal event
- `previous_response_id` continuation을 다른 Agentic replica에서 성공
- reasoning/function/custom tool item round trip
- GET Upgrade WebSocket과 HTTP fallback
- unknown model, backend 4xx/5xx, disconnect error fidelity

### P/D

- cell-local Router가 `/v1/responses` Prefill에 `max_output_tokens=1` 적용
- actual remote KV handoff와 Decode load 확인
- long-context SSE, cancellation, Prefill/Decode failure
- Cell replica 교체 뒤 Agentic continuation

### failure and operations

- PostgreSQL failover와 migration concurrency
- partial SSE 이후 edge replay 없음
- Agentic/Router/vLLM rolling drain
- Router stream memory와 connection-pool distribution
- image digest/source SHA 대조

## 11. 최종 판정

이 통합은 다음 조건으로 production candidate다.

```text
path split               = edge / vLLM Proxy
Responses durable state  = Agentic API + PostgreSQL
multi-model selection    = LMStack Router 0.1.9 normal round-robin
P/D orchestration        = separately pinned Responses-aware cell Router
Pod-level KV routing     = 후속 과제, direct endpoint ownership 필요
```

구현 범위를 “Agentic API 배포 + vLLM Proxy path routing”으로 시작하는 것은 타당하다. 완료 정의에는 POST뿐 아니라
WebSocket Upgrade, endpoint별 retry/timeout, image provenance, P/D exact contract test까지 포함한다.
