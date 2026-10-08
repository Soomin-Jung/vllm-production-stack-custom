# JEV REST ↔ MCP 계약

소스 검토: 2026-10-08 KST. 기준은 AutoTrust 모델 저장소 revision `51740a8891c2a8baefd969237fd44187b3e3a115`의 [serve_decide.py](https://huggingface.co/autotrust/JEV-27B/blob/51740a8891c2a8baefd969237fd44187b3e3a115/serve_decide.py)와 [모델 가이드](https://huggingface.co/autotrust/JEV-27B/blob/51740a8891c2a8baefd969237fd44187b3e3a115/README.md)다. MCP는 이 파일을 실행하거나 모델을 로드하지 않는다.

해당 `serve_decide.py`의 SHA-256은 `b4c95faf8619b32241394f68dd1b5b5a1702a94da96f59c5467d8de4367b5132`다. 요청 model의 13개 필드와 defaults를 이 revision에서 확인했다.

## 요청

`jev_decide`는 아래 필드를 REST와 같은 이름으로 받는다. 로컬 JSON 타입 검증은 엄격하며 알 수 없는 필드는 거부한다. 나머지 엔진별 validation은 백엔드에 맡기고 HTTP 오류를 MCP error로 전달한다.

| 필드 | 형식 | 생략 시 백엔드 의미 |
| --- | --- | --- |
| `kind` | `choice`, `noul`, `score` | 필수 |
| `state` | 문자열, JSON object, JSON list | 빈 문자열 |
| `question` | 문자열 | 필수 |
| `options` | 문자열 배열 또는 null | `choice`는 2–256개 필수. 실제 엔진 한도가 더 작으면 백엔드가 거부 |
| `strategy` | `auto`, `single`, `tournament`, `permute` | `auto`: 서버 profile |
| `thinking` | `default`, `off`, `auto`, `on` | `default`: 서버 profile |
| `threshold` | 유한 number 또는 null | profile threshold |
| `think_budget` | integer 또는 null | context window 내 백엔드 기본 budget |
| `reasoning_effort` | 문자열 또는 null | base model/template 설정 |
| `chat_template_kwargs` | JSON object 또는 null | 서버 template 기본값 |
| `return_reasoning` | boolean | false |
| `debug` | boolean | false |
| `system2_only` | boolean | false |

`model` 필드는 없다. `/v1/decide`는 시작 시 설정한 model/decision adapter를 사용한다. `reasoning_effort`를 주면 기준 소스의 서버는 동일 이름의 `chat_template_kwargs` 값보다 이를 우선한다. MCP는 이 결정을 재구현하지 않는다.

기준 소스는 `score`의 `thinking=auto/on`과 `system2_only=true`를 거부한다. 큰 `choice`의 strategy, reasoning budget, multimodal 처리는 백엔드가 수행한다. `state` list를 전송할 수 있어도 **text-only JEV-27B가 이미지를 이해하게 되는 것은 아니다**. vision-capable backend가 필요하다.

모델 저장소의 `main`은 변경된다. 10월 1일 가이드로 설치한 오래된 `serve_decide.py`는 일부 advanced controls를 지원하지 않을 수 있고 Pydantic 기본 동작으로 이를 무시할 수 있다. `jev_decide_info`로 capabilities를 확인하고 **배포한 소스 revision을 기록**한다. MCP가 필드를 전달한다는 사실만으로 adaptive thinking 지원을 판정하지 않는다. 기본 세 primitive는 기존 응답 계약과 호환된다.

## 결과 해석

| kind | options/probabilities 순서 | choice 의미 |
| --- | --- | --- |
| `choice` | 입력 options의 순서 | 최대 확률 옵션. `choice_index`는 0부터 시작 |
| `noul` | `["false", "true"]` | 문자열 `"false"` 또는 `"true"`. 참일 확률은 `probabilities[1]` |
| `score` | `["0", "1", "2", "3", "4", "5"]` | 최대 확률 점수인 문자열. 기대 점수와 다름 |

기대 점수는 클라이언트가 `sum(i * probabilities[i] for i in range(6))`로 구할 수 있지만 MCP는 이를 response에 추가하지 않는다. 확률은 제공된 옵션 내의 모델 분포이며 실제 업무에서의 정답 보증은 아니다. 중복 option 문자열도 원래 순서로 유지한다.

일반 응답의 `effective_kind`, `adaptation`, `protocol`, `model`, `usage`, `elapsed_seconds`, `num_model_requests`, 선택적 `thinking` 및 미래 추가 필드를 그대로 보존한다. `system2_only` 응답은 기준 소스에서 `system=2`이며 `protocol`, `effective_kind`, `adaptation`, `usage.total_tokens`가 없을 수 있다. MCP는 없는 값을 만들어 넣지 않는다.

MCP는 core 구조를 검사한다: 요청 kind/options 일치, 배열 길이, 유한 0–1 확률, 확률 합(절대 오차 0.001 이내), 유효한 index 및 최대값 선택, 일반 응답의 `jev27-bare-v1` 또는 System 2의 `system=2`. 동률은 기준 소스와 같이 첫 옵션이어야 한다. 모든 검사 후 원래 JSON을 반환한다. 이 검사는 다른 API에 잘못 연결되거나 깨진 응답이 성공 판정으로 사용되는 것을 막는다.

## 오류와 네트워크

오류는 `CallToolResult.isError=true`와 `{"error": ...}`로 반환한다. HTTP 400/401/404/422/500/503, 200 안의 error object, 비정상 JSON, contract 불일치, 연결 실패, timeout을 정상 decision으로 바꾸지 않는다. JSON upstream error body와 HTTP status는 tool error에 담되 서버 log에는 기록하지 않는다.

| 설정 | 의미 |
| --- | --- |
| `JEV_BASE_URL` | 필수. `http(s)://host[:port][/prefix]`, `/v1`, `/v1/decide` 모두 지원 |
| `JEV_API_KEY` | 선택. configured backend에만 Bearer header로 전송 |
| `JEV_TIMEOUT_SECONDS` | 기본 60. 요청 전체 deadline과 HTTP timeout |
| `JEV_CA_BUNDLE` | 선택. 사내 TLS CA 파일 경로 |

주소에 credential/query/fragment는 허용하지 않는다. URL prefix는 보존한다. redirect는 따라가지 않으며 자동 retry, proxy 환경변수 적용, 다른 provider 탐색을 하지 않는다. timeout/cancellation 때 로컬 HTTP 작업이 종료되어도 백엔드 GPU 작업 abort 여부는 백엔드 구현에 달려 있다.

현재 MCP transport는 official Python SDK 1.x를 사용한다. request fields는 REST와 동일하지만 **jev-code의 `jev_classify/jev_check/jev_score/jev_rank/jev_ask`와 동일한 tool schema가 아니다**. 기존 jev-code skill을 그대로 연결하지 말고 [클라이언트 지침](clients.md)을 사용한다. 특히 `rank`는 native endpoint primitive가 아니므로 임의의 listwise ranking으로 변환하지 않는다.
