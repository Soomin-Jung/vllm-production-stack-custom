# Issue #6 — Mooncake v0.3.10 minimal Driver-API IPC fix candidate

## 목적

이 문서는 `Mooncake v0.3.10`의
`IntraNodeNvlinkTransport` 원본과 Issue #6용 최소 수정본의 차이를 설명한다.

이번 단계의 목표는 더 많은 진단 코드를 넣어 원인을 추적하는 것이 아니다.

대신 기존 `nvlink_intra` 데이터 경로를 최대한 유지하면서,
간헐적으로 발생하는 다음 실패 경계를 하나의 좁은 변경으로 우회한다.

```text
cudaIpcOpenMemHandle(...)
  -> 201 / invalid device context
```

기준 upstream:

```text
kvcache-ai/Mooncake
tag: v0.3.10
file:
mooncake-transfer-engine/src/transport/intranode_nvlink_transport/
  intranode_nvlink_transport.cpp

upstream blob:
bf9bfb638b2150eb7adf3f2395e8140a374d59a3
```

수정 파일:

```text
debug/issue-6/mooncake-v0.3.10/
  intranode_nvlink_transport.debug.cpp
```

## 변경 원칙

이번 수정은 다음을 변경하지 않는다.

- Mooncake `MultiTransport` 선택 구조
- `nvlink_intra` protocol
- local/remote segment metadata 구조
- `BufferDesc.addr` / `length` 의미
- cudaMalloc base address 탐색
- remap cache key
- `submitTransfer()` 로직
- `submitTransferTask()` 로직
- 실제 KV copy의 `cudaMemcpy(..., cudaMemcpyDefault)`
- P2P/NVLink data path
- vLLM KV layout
- vLLM request scheduling
- transfer batching 정책

즉 데이터 이동 구조 자체를 재설계하지 않는다.

변경하는 것은 **CUDA IPC handle lifecycle API family 하나뿐**이다.

---

# 1. 원본 v0.3.10 구조

## Export side

원본은 remote-accessible GPU allocation에 대해:

```cpp
cudaIpcMemHandle_t handle;
cudaIpcGetMemHandle(&handle, (void *)base_ptr);
```

를 수행하고 handle bytes를 `BufferDesc.shm_name`에 serialize한다.

## Import side

remote address가 처음 사용될 때:

```cpp
cudaIpcMemHandle_t handle;
...
cudaIpcOpenMemHandle(
    &shm_addr,
    handle,
    cudaIpcMemLazyEnablePeerAccess);
```

를 호출한다.

성공한 mapping은:

```text
(target_id, remote_base)
    ->
local mapped address
```

형태로 `remap_entries_`에 저장된다.

## Close side

transport destructor에서:

```cpp
cudaIpcCloseMemHandle(...)
```

로 imported mapping을 해제한다.

즉 원본의 IPC lifecycle은 전부 CUDA Runtime API이다.

```text
cudaIpcGetMemHandle
        |
        v
 serialized metadata
        |
        v
cudaIpcOpenMemHandle
        |
        v
cudaIpcCloseMemHandle
```

---

# 2. 이번 수정 구조

이번 수정에서는 **IPC lifecycle만 CUDA Driver API로 통일**한다.

```text
cuIpcGetMemHandle
        |
        v
 serialized metadata
        |
        v
cuIpcOpenMemHandle
        |
        v
cuIpcCloseMemHandle
```

실제 데이터 복사는 여전히:

```cpp
cudaMemcpy(..., cudaMemcpyDefault);
```

를 사용한다.

CUDA Driver/Runtime interop에서는 current Driver context를 Runtime이 함께 사용할 수 있기 때문에,
IPC mapping 생성만 Driver API로 수행하고 copy path는 기존 Runtime API를 유지한다.

---

# 3. Export 변경

## 원본

```cpp
cudaIpcMemHandle_t handle;

err = cudaIpcGetMemHandle(
    &handle,
    (void *)base_ptr);

desc.shm_name = serializeBinaryData(
    &handle,
    sizeof(cudaIpcMemHandle_t));
```

## 수정

```cpp
CUipcMemHandle handle;

cu_err = cuIpcGetMemHandle(
    &handle,
    base_ptr);

desc.shm_name = serializeBinaryData(
    &handle,
    sizeof(CUipcMemHandle));
```

### 의미

GPU allocation, base address, allocation size는 바뀌지 않는다.

단지 동일한 CUDA IPC mechanism을 Runtime wrapper가 아니라
Driver API entry point로 호출한다.

---

# 4. Import 변경

## 원본

```cpp
cudaIpcMemHandle_t handle;
void *shm_addr = nullptr;

cudaError_t err = cudaIpcOpenMemHandle(
    &shm_addr,
    handle,
    cudaIpcMemLazyEnablePeerAccess);
```

## 수정

먼저 current Driver context가 실제로 존재하는지만 확인한다.

```cpp
CUcontext current_ctx = nullptr;

CUresult ctx_rc =
    cuCtxGetCurrent(&current_ctx);

if (ctx_rc != CUDA_SUCCESS ||
    current_ctx == nullptr) {
    return -1;
}
```

그 다음 동일한 lazy peer-access 의미로 Driver API를 사용한다.

```cpp
CUdeviceptr mapped_addr = 0;

CUresult open_rc = cuIpcOpenMemHandle(
    &mapped_addr,
    handle,
    CU_IPC_MEM_LAZY_ENABLE_PEER_ACCESS);
```

성공하면 기존 `OpenedShmEntry` 구조에 그대로 저장한다.

```cpp
shm_entry.shm_addr =
    reinterpret_cast<void *>(mapped_addr);
```

### 중요한 점

이번 수정은 새로운 CUDA context를 생성하지 않는다.

다음을 호출하지 않는다.

```text
cuCtxCreate
cuDevicePrimaryCtxRetain
cudaSetDevice
cudaDeviceEnablePeerAccess
```

즉 vLLM/PyTorch가 이미 사용하고 있는 **현재 CUcontext를 그대로 사용한다.**

이는 NVIDIA CUDA IPC의 다음 전제와 맞춘다.

```text
cuIpcOpenMemHandle maps the exported memory
into the current device address space.
```

또한 기존 Runtime API와 동일하게:

```text
CU_IPC_MEM_LAZY_ENABLE_PEER_ACCESS
```

를 유지하므로 peer-access 의미도 바꾸지 않는다.

---

# 5. Close 변경

## 원본

```cpp
cudaIpcCloseMemHandle(
    entry.second.shm_addr);
```

## 수정

```cpp
cuIpcCloseMemHandle(
    reinterpret_cast<CUdeviceptr>(
        entry.second.shm_addr));
```

Open을 Driver API로 수행했기 때문에 close까지 같은 API family로 유지한다.

---

# 6. 왜 이 방향을 먼저 시도하는가

이번 Issue #6에서는 실제 실패가:

```text
current CUcontext exists
        |
        v
cudaIpcOpenMemHandle()
        |
        v
201 / invalid device context
```

형태로 나타났다.

중요한 점은 Runtime API의 IPC open이 실패하는 반면,
그 직전 Driver API context query는 정상적인 current CUcontext를 보고했다는 것이다.

따라서 이번 수정의 목적은:

```text
CUDA Runtime IPC bookkeeping
        |
        X  제거
        |
current CUcontext
        |
        v
CUDA Driver IPC API
```

처럼 **문제가 발생하는 boundary를 좁히는 것**이다.

이것은 root cause를 확정했다는 의미가 아니다.

다만 다음 두 경우를 명확히 분리할 수 있다.

### Driver-API build에서 안정화되는 경우

```text
Runtime IPC path의 context/runtime interaction
또는 wrapper-side state가 failure condition에 관여
```

했다고 볼 근거가 강해진다.

### Driver-API build에서도 동일하게 201이 나는 경우

```text
cuIpcOpenMemHandle -> CUDA_ERROR_INVALID_CONTEXT
```

가 직접 확인되므로,
문제는 Runtime wrapper보다 아래인 Driver/context/IPC ownership 영역으로 내려간다.

---

# 7. 성능 영향

정상 경로에서 추가되는 것은 사실상 없다.

기존:

```text
cudaIpcOpenMemHandle
```

대신:

```text
cuCtxGetCurrent
cuIpcOpenMemHandle
```

를 **remote allocation의 최초 mapping 시점**에만 수행한다.

mapping이 `remap_entries_`에 캐시된 뒤에는 기존과 동일하게:

```text
remap cache hit
    ->
address relocation
    ->
cudaMemcpy
```

로 진행한다.

따라서 steady-state KV copy마다 Driver API open을 호출하는 구조가 아니다.

실제 GPU data path도 그대로 NVLink/P2P이다.

```text
GPU memory
   |
CUDA IPC mapping
   |
cudaMemcpyDefault
   |
NVLink / peer path
```

CPU staging, TCP fallback, RDMA fallback은 추가하지 않는다.

---

# 8. 이번 수정에서 의도적으로 하지 않은 것

다음은 이번 candidate에 포함하지 않는다.

- per-device CUDA stream pool backport
- async batch memcpy backport
- CUDA event synchronization backport
- all-visible-GPU peer access pre-enable
- `cudaSetDevice()` 반복 호출
- 별도 CUcontext 생성
- primary context 강제 retain
- retry loop
- sleep/backoff
- fallback transport
- metadata format 확장
- remap cache 구조 변경

이유는 한 번에 여러 동작을 바꾸면
안정화되더라도 어떤 변경이 실제 효과를 냈는지 알 수 없고,
성능 regression 가능성도 커지기 때문이다.

---

# 9. 테스트 기준

이 build에서는 별도 analyzer를 우선 사용하지 않아도 된다.

가장 중요한 것은 실제 운영 경로의 반복 성공 여부다.

권장 순서:

```text
1. fresh Helm install
2. P1D1 Ready
3. real inference / KV transfer
4. whole-cell recycle
5. real inference / KV transfer
6. 반복
```

우선:

```text
10 generations
```

으로 smoke/stability 확인.

통과하면:

```text
30~50 generations
```

으로 확대한다.

그리고 별도로:

- successful transfer count
- failed transfer count
- Avg/P90 transfer latency
- throughput

을 기존 successful baseline과 비교한다.

---

# 10. 실패 시 필요한 로그

이 candidate가 실패하면 기존 NVDBG 전체 로그는 필요하지 않다.

다음 에러만 필요하다.

```text
IntraNodeNvlinkTransport: cuIpcOpenMemHandle failed:
<CUresult code/name/message>
```

만약:

```text
201 (CUDA_ERROR_INVALID_CONTEXT)
```

가 그대로 나오면 Runtime wrapper는 원인이 아니며,
다음 단계는 CUDA IPC context ownership을 직접 제어하는 별도 candidate로 이동한다.

---

## 현재 상태

```text
candidate type:
  minimal stabilization patch

baseline:
  Mooncake v0.3.10

changed:
  CUDA IPC API family only

unchanged:
  nvlink_intra data path
  metadata routing
  remap cache
  transfer scheduling
  cudaMemcpy copy path

status:
  requires runtime validation
```
