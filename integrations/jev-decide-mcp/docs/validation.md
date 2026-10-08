# 구현 검증 기록

검증일: 2026-10-08 KST. Python 3.12.14, MCP Python SDK 1.30.0, httpx 0.28.1, Pydantic 2.13.5.

## 확인한 항목

- upstream 소스 revision/해시와 `DecideRequest`의 13개 필드 확인: [API 계약](contract.md).
- pytest 63개 통과: 세 primitive, string/object/list state, 2–256 options, 중복 option 유지, 명시한 null/false, 생략 필드 유지, advanced controls 전송, System 2 변형 response 보존.
- malformed response/확률 분포/choice 불일치, HTTP 301/400/401/404/422/500/503, 200 안 error, 연결 실패와 전체 timeout 검사.
- 공식 SDK로 실제 subprocess stdio의 initialize → list_tools → call_tool과 JSON text/structured output 동일성 확인.
- 실제 localhost HTTP socket의 MCP initialize → list_tools → call_tool, Bearer token 거부/허용 및 Host allowlist 검사.
- stdout에 일반 log가 섞이지 않고 evidence와 upstream error body가 stderr에 기록되지 않는 것 확인.
- wheel/sdist build, Ruff lint/format, 전체 저장소 Markdown/link validator 통과.
- Claude JSON/Codex TOML 예제 syntax parse 통과.

mock backend는 protocol/contract 통신 검증용이며 모델을 실행하지 않는다. Docker build, 사용자 실제 vLLM 접속, 실제 GPU 정확도/성능, Claude Code/Codex UI에서의 도구 선택은 이 기록의 검증 범위에 포함하지 않는다. Python 3.11/3.12 matrix는 PR CI에 추가했으며 원격 실행 결과는 PR checks에서 확인한다.

## 사용자가 배포 후 수행할 검증

1. `JEV_BASE_URL`에 배포된 접근 가능한 주소를 넣고 [smoke script](../examples/smoke.py)를 실행한다.
2. [클라이언트 등록](clients.md) 후 `/mcp`로 tools를 확인한다.
3. tool 호출을 명시한 첫 업무 사례를 실행하고 backend access log/usage로 실제 JEV inference를 확인한다.
4. [운영 문서](operations.md)의 baseline 비교 기준으로 업무 효과를 측정한다.
