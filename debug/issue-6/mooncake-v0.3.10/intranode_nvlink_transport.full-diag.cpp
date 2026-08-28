// DIAGNOSTIC BUILD FOR vllm-production-stack-custom Issue #6
// Based on upstream kvcache-ai/Mooncake v0.3.10 commit/blob:
// intranode_nvlink_transport.cpp sha bf9bfb638b2150eb7adf3f2395e8140a374d59a3
//
// Purpose:
//   Capture full IntraNodeNvlinkTransport CUDA IPC lifecycle for
//   intermittent cudaIpcOpenMemHandle(CUDA_ERROR_INVALID_CONTEXT=201).
//
// IMPORTANT:
//   This file intentionally DOES NOT call cudaSetDevice(), cuCtxSetCurrent(),
//   or otherwise attempt to fix context state. It is instrumentation only.
//   Logging is intentionally verbose and is not intended for production.
//

// Copyright 2024 KVCache.AI
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "transport/intranode_nvlink_transport/intranode_nvlink_transport.h"

#include <bits/stdint-uintn.h>
#include "cuda_alike.h"
#include <glog/logging.h>

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <memory>
#include <sstream>
#include <string>
#include <sys/syscall.h>
#include <unistd.h>

namespace {

static std::atomic<uint64_t> g_diag_sequence{0};
static thread_local uint64_t g_diag_tls_sequence = 0;

static uint64_t nextDiagSequence() {
    return g_diag_sequence.fetch_add(1, std::memory_order_relaxed) + 1;
}

class ScopedDiagSequence {
   public:
    explicit ScopedDiagSequence(uint64_t seq)
        : previous_(g_diag_tls_sequence) {
        g_diag_tls_sequence = seq;
    }
    ~ScopedDiagSequence() { g_diag_tls_sequence = previous_; }

   private:
    uint64_t previous_;
};

static uint64_t currentDiagSequence() {
    return g_diag_tls_sequence;
}

static long diagTid() {
    return static_cast<long>(::syscall(SYS_gettid));
}

static const char *envOrUnset(const char *name) {
    const char *value = std::getenv(name);
    return value ? value : "(unset)";
}

static std::string cuResultToString(CUresult rc) {
    const char *name = nullptr;
    const char *message = nullptr;
    cuGetErrorName(rc, &name);
    cuGetErrorString(rc, &message);

    std::ostringstream out;
    out << static_cast<int>(rc)
        << "/" << (name ? name : "UNKNOWN")
        << "/" << (message ? message : "UNKNOWN");
    return out.str();
}

static uint64_t fnv1a64(const void *data, size_t length) {
    const auto *bytes = static_cast<const unsigned char *>(data);
    uint64_t hash = 1469598103934665603ULL;
    for (size_t i = 0; i < length; ++i) {
        hash ^= static_cast<uint64_t>(bytes[i]);
        hash *= 1099511628211ULL;
    }
    return hash;
}

static std::string bytesSignature(const void *data, size_t length,
                                  size_t prefix_length = 16) {
    const auto *bytes = static_cast<const unsigned char *>(data);
    std::ostringstream out;
    out << "hash=0x" << std::hex << fnv1a64(data, length) << " prefix=";
    const size_t n = std::min(length, prefix_length);
    for (size_t i = 0; i < n; ++i) {
        if (i) out << ":";
        out << std::setw(2) << std::setfill('0')
            << static_cast<unsigned int>(bytes[i]);
    }
    return out.str();
}

static std::string pointerAttributesToString(const void *ptr) {
    if (ptr == nullptr) return "ptr=null";

    cudaPointerAttributes attr{};
    cudaError_t rc = cudaPointerGetAttributes(&attr, ptr);

    std::ostringstream out;
    out << "ptr=" << ptr
        << " ptr_attr_rc=" << static_cast<int>(rc)
        << "/" << cudaGetErrorString(rc);

    if (rc == cudaSuccess) {
        out << " ptr_type=" << static_cast<int>(attr.type)
            << " ptr_device=" << attr.device
            << " device_ptr=" << attr.devicePointer
            << " host_ptr=" << attr.hostPointer;
    }
    return out.str();
}

static void logCudaContext(const char *where, const void *ptr = nullptr,
                           uint64_t sequence = 0) {
    if (sequence == 0) sequence = currentDiagSequence();

    CUcontext context = nullptr;
    CUresult ctx_rc = cuCtxGetCurrent(&context);

    int ctx_device = -1;
    CUresult ctx_device_rc = CUDA_ERROR_INVALID_CONTEXT;
    if (ctx_rc == CUDA_SUCCESS && context != nullptr) {
        ctx_device_rc = cuCtxGetDevice(&ctx_device);
    }

    int runtime_device = -1;
    cudaError_t runtime_device_rc = cudaGetDevice(&runtime_device);

    LOG(INFO) << "[INTRA_NVLINK_DIAG][CTX]"
              << " seq=" << sequence
              << " pid=" << static_cast<long>(::getpid())
              << " tid=" << diagTid()
              << " where=" << where
              << " cu_ctx=" << context
              << " cu_ctx_rc=" << cuResultToString(ctx_rc)
              << " cu_ctx_device=" << ctx_device
              << " cu_ctx_device_rc=" << cuResultToString(ctx_device_rc)
              << " runtime_device=" << runtime_device
              << " runtime_device_rc=" << static_cast<int>(runtime_device_rc)
              << "/" << cudaGetErrorString(runtime_device_rc)
              << " CUDA_VISIBLE_DEVICES=" << envOrUnset("CUDA_VISIBLE_DEVICES")
              << " NVIDIA_VISIBLE_DEVICES="
              << envOrUnset("NVIDIA_VISIBLE_DEVICES")
              << " " << pointerAttributesToString(ptr);
}

static const char *opcodeToString(int opcode) {
    return opcode == 0 ? "READ" : "WRITE";
}

}  // namespace

#include "common.h"
#include "common/serialization.h"
#include "config.h"
#include "transfer_engine.h"
#include "transfer_metadata.h"
#include "transport/transport.h"

static bool checkCudaErrorReturn(cudaError_t result, const char *message) {
    if (result != cudaSuccess) {
        LOG(ERROR) << message << " (Error code: " << result << " - "
                   << cudaGetErrorString(result) << ")" << std::endl;
        return false;
    }
    return true;
}

namespace mooncake {
static int getNumDevices() {
    static int cached_num_devices = -1;
    if (cached_num_devices == -1) {
        if (!checkCudaErrorReturn(
                cudaGetDeviceCount(&cached_num_devices),
                "IntraNodeNvlinkTransport: cudaGetDeviceCount failed")) {
            return 0;
        }
    }
    return cached_num_devices;
}

static bool enableP2PAccess(int src_device_id, int dst_device_id) {
    int canAccessPeer = 0;
    if (!checkCudaErrorReturn(
            cudaDeviceCanAccessPeer(&canAccessPeer, src_device_id,
                                    dst_device_id),
            "IntraNodeNvlinkTransport: failed to query peer access")) {
        return false;
    }

    if (!canAccessPeer) {
        LOG(ERROR) << "IntraNodeNvlinkTransport: device " << src_device_id
                   << " cannot p2p access device " << dst_device_id;
        return false;
    }

    // enable src->dst p2p access
    if (!checkCudaErrorReturn(
            cudaSetDevice(src_device_id),
            "IntraNodeNvlinkTransport: failed to set device")) {
        return false;
    }
    cudaError_t result = cudaDeviceEnablePeerAccess(dst_device_id, 0);

    if (result != cudaSuccess && result != cudaErrorPeerAccessAlreadyEnabled) {
        LOG(ERROR) << "IntraNodeNvlinkTransport: failed to enable p2p access "
                      "(Error code: "
                   << result << " - " << cudaGetErrorString(result) << ")"
                   << std::endl;

        return false;
    }

    // enable dst->src p2p access
    if (!checkCudaErrorReturn(
            cudaSetDevice(dst_device_id),
            "IntraNodeNvlinkTransport: failed to set device")) {
        return false;
    }
    result = cudaDeviceEnablePeerAccess(src_device_id, 0);

    if (result != cudaSuccess && result != cudaErrorPeerAccessAlreadyEnabled) {
        LOG(ERROR) << "IntraNodeNvlinkTransport: failed to enable p2p access "
                      "(Error code: "
                   << result << " - " << cudaGetErrorString(result) << ")"
                   << std::endl;

        return false;
    }

    return true;
}
IntraNodeNvlinkTransport::IntraNodeNvlinkTransport() {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][CTOR]"
              << " seq=" << seq
              << " this=" << this
              << " pid=" << static_cast<long>(::getpid())
              << " tid=" << diagTid()
              << " num_devices=" << getNumDevices();
    logCudaContext("constructor");
}

// IntraNodeNvlinkTransport::IntraNodeNvlinkTransport() :
// use_fabric_mem_(supportFabricMem()) {}
//     int num_devices = getNumDevices();
//     if (globalConfig().trace) {
//         LOG(INFO) << "IntraNodeNvlinkTransport: use_fabric_mem_:" <<
//         use_fabric_mem_
//                   << ", num_devices: " << num_devices;
//     }

//     for (int src_device_id = 0; src_device_id < num_devices; ++src_device_id)
//     {
//         for (int dst_device_id = src_device_id + 1; dst_device_id <
//         num_devices;
//              ++dst_device_id) {
//             if (enableP2PAccess(src_device_id, dst_device_id)) {
//                 if (globalConfig().trace) {
//                     LOG(INFO)
//                         << "IntraNodeNvlinkTransport: enabled p2p access
//                         between device "
//                         << src_device_id << " and " << dst_device_id;
//                 }
//             } else {
//                 LOG(ERROR) << "IntraNodeNvlinkTransport: failed to enable p2p
//                 access "
//                               "between device "
//                            << src_device_id << " and " << dst_device_id;
//             }
//         }
//     }
// }

IntraNodeNvlinkTransport::~IntraNodeNvlinkTransport() {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][DTOR_BEGIN]"
              << " seq=" << seq
              << " this=" << this
              << " remap_entries=" << remap_entries_.size();
    logCudaContext("destructor:begin");

    for (auto &entry : remap_entries_) {
        const auto &key = entry.first;
        const auto &value = entry.second;
        LOG(INFO) << "[INTRA_NVLINK_DIAG][IPC_CLOSE_BEGIN]"
                  << " seq=" << seq
                  << " target_id=" << key.first
                  << " remote_base=0x" << std::hex << key.second << std::dec
                  << " mapped_addr=" << value.shm_addr
                  << " length=" << value.length;
        cudaError_t rc = cudaIpcCloseMemHandle(value.shm_addr);
        LOG(INFO) << "[INTRA_NVLINK_DIAG][IPC_CLOSE_END]"
                  << " seq=" << seq
                  << " target_id=" << key.first
                  << " remote_base=0x" << std::hex << key.second << std::dec
                  << " rc=" << static_cast<int>(rc)
                  << "/" << cudaGetErrorString(rc);
    }
    remap_entries_.clear();
    logCudaContext("destructor:end");
    LOG(INFO) << "[INTRA_NVLINK_DIAG][DTOR_END]"
              << " seq=" << seq
              << " this=" << this;
}

int IntraNodeNvlinkTransport::install(
    std::string &local_server_name, std::shared_ptr<TransferMetadata> metadata,
    std::shared_ptr<Topology> topology) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    metadata_ = metadata;
    local_server_name_ = local_server_name;

    LOG(INFO) << "[INTRA_NVLINK_DIAG][INSTALL_BEGIN]"
              << " seq=" << seq
              << " this=" << this
              << " local_server_name=" << local_server_name_
              << " metadata=" << metadata_.get()
              << " topology=" << topology.get();
    logCudaContext("install:begin");

    auto desc = std::make_shared<SegmentDesc>();
    if (!desc) return ERR_MEMORY;
    desc->name = local_server_name_;
    desc->protocol = "nvlink_intra";
    int rc = metadata_->addLocalSegment(LOCAL_SEGMENT_ID, local_server_name_,
                                        std::move(desc));
    LOG(INFO) << "[INTRA_NVLINK_DIAG][INSTALL_END]"
              << " seq=" << seq
              << " local_segment_id=" << LOCAL_SEGMENT_ID
              << " rc=" << rc;
    logCudaContext("install:end");
    return rc;
}

Status IntraNodeNvlinkTransport::submitTransfer(
    BatchID batch_id, const std::vector<TransferRequest> &entries) {
    const uint64_t batch_seq = nextDiagSequence();
    ScopedDiagSequence batch_scope(batch_seq);
    auto &batch_desc = *((BatchDesc *)(batch_id));

    LOG(INFO) << "[INTRA_NVLINK_DIAG][SUBMIT_BEGIN]"
              << " seq=" << batch_seq
              << " batch_id=" << batch_id
              << " incoming_entries=" << entries.size()
              << " existing_tasks=" << batch_desc.task_list.size()
              << " batch_size=" << batch_desc.batch_size;
    logCudaContext("submitTransfer:begin");

    if (batch_desc.task_list.size() + entries.size() > batch_desc.batch_size) {
        LOG(ERROR) << "[INTRA_NVLINK_DIAG][SUBMIT_REJECT]"
                   << " seq=" << batch_seq
                   << " reason=capacity"
                   << " existing_tasks=" << batch_desc.task_list.size()
                   << " incoming_entries=" << entries.size()
                   << " batch_size=" << batch_desc.batch_size;
        return Status::InvalidArgument(
            "IntraNodeNvlinkTransport: Exceed the limitation of capacity, "
            "batch id: " +
            std::to_string(batch_id));
    }

    size_t task_id = batch_desc.task_list.size();
    batch_desc.task_list.resize(task_id + entries.size());

    size_t entry_index = 0;
    for (auto &request : entries) {
        const uint64_t seq = nextDiagSequence();
        ScopedDiagSequence request_scope(seq);
        TransferTask &task = batch_desc.task_list[task_id];
        ++task_id;

        const uint64_t original_dest_addr = request.target_offset;
        uint64_t dest_addr = original_dest_addr;

        LOG(INFO) << "[INTRA_NVLINK_DIAG][REQUEST]"
                  << " seq=" << seq
                  << " path=submitTransfer"
                  << " entry_index=" << entry_index
                  << " batch_id=" << batch_id
                  << " task=" << &task
                  << " opcode=" << opcodeToString(request.opcode)
                  << " source=" << request.source
                  << " target_id=" << request.target_id
                  << " target_offset=0x" << std::hex << original_dest_addr
                  << std::dec
                  << " length=" << request.length
                  << " source_attrs={" << pointerAttributesToString(request.source)
                  << "}";
        logCudaContext("submitTransfer:before-relocate", request.source, seq);

        if (request.target_id != LOCAL_SEGMENT_ID) {
            int rc = relocateSharedMemoryAddress(dest_addr, request.length,
                                                 request.target_id);
            LOG(INFO) << "[INTRA_NVLINK_DIAG][RELOCATE_RESULT]"
                      << " seq=" << seq
                      << " rc=" << rc
                      << " original_dest=0x" << std::hex << original_dest_addr
                      << " relocated_dest=0x" << dest_addr << std::dec
                      << " target_id=" << request.target_id
                      << " length=" << request.length;
            if (rc) {
                logCudaContext("submitTransfer:relocate-failed",
                               request.source, seq);
                return Status::Memory("device memory not registered");
            }
        }

        task.total_bytes = request.length;
        Slice *slice = getSliceCache().allocate();
        slice->source_addr = (char *)request.source;
        slice->local.dest_addr = (char *)dest_addr;
        slice->length = request.length;
        slice->opcode = request.opcode;
        slice->task = &task;
        slice->target_id = request.target_id;
        slice->status = Slice::PENDING;
        __sync_fetch_and_add(&task.slice_count, 1);

        LOG(INFO) << "[INTRA_NVLINK_DIAG][COPY_BEGIN]"
                  << " seq=" << seq
                  << " opcode=" << opcodeToString(slice->opcode)
                  << " source=" << static_cast<void *>(slice->source_addr)
                  << " dest=" << static_cast<void *>(slice->local.dest_addr)
                  << " length=" << slice->length
                  << " source_attrs={"
                  << pointerAttributesToString(slice->source_addr) << "}"
                  << " dest_attrs={"
                  << pointerAttributesToString(slice->local.dest_addr) << "}";
        logCudaContext("submitTransfer:before-copy", request.source, seq);

        cudaError_t err;
        if (slice->opcode == TransferRequest::READ)
            err = cudaMemcpy(slice->source_addr, (void *)slice->local.dest_addr,
                             slice->length, cudaMemcpyDefault);
        else
            err = cudaMemcpy((void *)slice->local.dest_addr, slice->source_addr,
                             slice->length, cudaMemcpyDefault);

        LOG(INFO) << "[INTRA_NVLINK_DIAG][COPY_END]"
                  << " seq=" << seq
                  << " rc=" << static_cast<int>(err)
                  << "/" << cudaGetErrorString(err)
                  << " opcode=" << opcodeToString(slice->opcode)
                  << " length=" << slice->length;
        logCudaContext("submitTransfer:after-copy", request.source, seq);

        if (err != cudaSuccess)
            slice->markFailed();
        else
            slice->markSuccess();

        ++entry_index;
    }

    LOG(INFO) << "[INTRA_NVLINK_DIAG][SUBMIT_END]"
              << " seq=" << batch_seq
              << " batch_id=" << batch_id
              << " entries=" << entries.size();
    return Status::OK();
}

Status IntraNodeNvlinkTransport::getTransferStatus(BatchID batch_id,
                                                   size_t task_id,
                                                   TransferStatus &status) {
    auto &batch_desc = *((BatchDesc *)(batch_id));
    const size_t task_count = batch_desc.task_list.size();
    if (task_id >= task_count) {
        return Status::InvalidArgument(
            "IntraNodeNvlinkTransport::getTransportStatus invalid argument, "
            "batch id: " +
            std::to_string(batch_id));
    }
    auto &task = batch_desc.task_list[task_id];
    status.transferred_bytes = task.transferred_bytes;
    uint64_t success_slice_count = task.success_slice_count;
    uint64_t failed_slice_count = task.failed_slice_count;
    if (success_slice_count + failed_slice_count == task.slice_count) {
        if (failed_slice_count) {
            status.s = TransferStatusEnum::FAILED;
        } else {
            status.s = TransferStatusEnum::COMPLETED;
        }
        task.is_finished = true;
        LOG(INFO) << "[INTRA_NVLINK_DIAG][TRANSFER_STATUS_TERMINAL]"
                  << " batch_id=" << batch_id
                  << " task_id=" << task_id
                  << " transferred_bytes=" << status.transferred_bytes
                  << " slice_count=" << task.slice_count
                  << " success_slices=" << success_slice_count
                  << " failed_slices=" << failed_slice_count
                  << " result="
                  << (failed_slice_count ? "FAILED" : "COMPLETED");
    } else {
        status.s = TransferStatusEnum::WAITING;
    }
    return Status::OK();
}

Status IntraNodeNvlinkTransport::submitTransferTask(
    const std::vector<TransferTask *> &task_list) {
    const uint64_t list_seq = nextDiagSequence();
    ScopedDiagSequence list_scope(list_seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][TASK_LIST_BEGIN]"
              << " seq=" << list_seq
              << " task_count=" << task_list.size();
    logCudaContext("submitTransferTask:list-begin");

    for (size_t index = 0; index < task_list.size(); ++index) {
        const uint64_t seq = nextDiagSequence();
        ScopedDiagSequence request_scope(seq);

        assert(task_list[index]);
        auto &task = *task_list[index];
        assert(task.request);
        auto &request = *task.request;

        const uint64_t original_dest_addr = request.target_offset;
        uint64_t dest_addr = original_dest_addr;

        LOG(INFO) << "[INTRA_NVLINK_DIAG][REQUEST]"
                  << " seq=" << seq
                  << " path=submitTransferTask"
                  << " list_seq=" << list_seq
                  << " task_index=" << index
                  << " task=" << &task
                  << " request=" << task.request
                  << " opcode=" << opcodeToString(request.opcode)
                  << " source=" << request.source
                  << " target_id=" << request.target_id
                  << " target_offset=0x" << std::hex << original_dest_addr
                  << std::dec
                  << " length=" << request.length
                  << " source_attrs={" << pointerAttributesToString(request.source)
                  << "}";
        logCudaContext("submitTransferTask:before-relocate",
                       request.source, seq);

        if (request.target_id != LOCAL_SEGMENT_ID) {
            int rc = relocateSharedMemoryAddress(dest_addr, request.length,
                                                 request.target_id);
            LOG(INFO) << "[INTRA_NVLINK_DIAG][RELOCATE_RESULT]"
                      << " seq=" << seq
                      << " rc=" << rc
                      << " original_dest=0x" << std::hex << original_dest_addr
                      << " relocated_dest=0x" << dest_addr << std::dec
                      << " target_id=" << request.target_id
                      << " length=" << request.length;
            if (rc) {
                logCudaContext("submitTransferTask:relocate-failed",
                               request.source, seq);
                return Status::Memory("device memory not registered");
            }
        }

        task.total_bytes = request.length;
        Slice *slice = getSliceCache().allocate();
        slice->source_addr = (char *)request.source;
        slice->local.dest_addr = (char *)dest_addr;
        slice->length = request.length;
        slice->opcode = request.opcode;
        slice->task = &task;
        slice->target_id = request.target_id;
        slice->status = Slice::PENDING;
        task.slice_list.push_back(slice);
        __sync_fetch_and_add(&task.slice_count, 1);

        LOG(INFO) << "[INTRA_NVLINK_DIAG][COPY_BEGIN]"
                  << " seq=" << seq
                  << " opcode=" << opcodeToString(slice->opcode)
                  << " source=" << static_cast<void *>(slice->source_addr)
                  << " dest=" << static_cast<void *>(slice->local.dest_addr)
                  << " length=" << slice->length
                  << " source_attrs={"
                  << pointerAttributesToString(slice->source_addr) << "}"
                  << " dest_attrs={"
                  << pointerAttributesToString(slice->local.dest_addr) << "}";
        logCudaContext("submitTransferTask:before-copy",
                       request.source, seq);

        cudaError_t err;
        if (slice->opcode == TransferRequest::READ)
            err = cudaMemcpy(slice->source_addr, (void *)slice->local.dest_addr,
                             slice->length, cudaMemcpyDefault);
        else
            err = cudaMemcpy((void *)slice->local.dest_addr, slice->source_addr,
                             slice->length, cudaMemcpyDefault);

        LOG(INFO) << "[INTRA_NVLINK_DIAG][COPY_END]"
                  << " seq=" << seq
                  << " rc=" << static_cast<int>(err)
                  << "/" << cudaGetErrorString(err)
                  << " opcode=" << opcodeToString(slice->opcode)
                  << " length=" << slice->length;
        logCudaContext("submitTransferTask:after-copy",
                       request.source, seq);

        if (err != cudaSuccess)
            slice->markFailed();
        else
            slice->markSuccess();
    }

    LOG(INFO) << "[INTRA_NVLINK_DIAG][TASK_LIST_END]"
              << " seq=" << list_seq
              << " task_count=" << task_list.size();
    return Status::OK();
}

int IntraNodeNvlinkTransport::registerLocalMemory(
    void *addr, size_t length, const std::string &location,
    bool remote_accessible, bool update_metadata) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    std::lock_guard<std::mutex> lock(register_mutex_);

    LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_BEGIN]"
              << " seq=" << seq
              << " addr=" << addr
              << " length=" << length
              << " location=" << location
              << " remote_accessible=" << remote_accessible
              << " update_metadata=" << update_metadata
              << " registered_base_count=" << registered_base_addrs_.size();
    logCudaContext("registerLocalMemory:begin", addr, seq);

    cudaPointerAttributes attr{};
    cudaError_t err = cudaPointerGetAttributes(&attr, addr);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_PTR_ATTR]"
              << " seq=" << seq
              << " addr=" << addr
              << " rc=" << static_cast<int>(err)
              << "/" << cudaGetErrorString(err)
              << " type=" << (err == cudaSuccess ? static_cast<int>(attr.type) : -1)
              << " device=" << (err == cudaSuccess ? attr.device : -1)
              << " device_ptr=" << (err == cudaSuccess ? attr.devicePointer : nullptr)
              << " host_ptr=" << (err == cudaSuccess ? attr.hostPointer : nullptr);
    if (err != cudaSuccess) {
        LOG(ERROR) << "[INTRA_NVLINK_DIAG][REGISTER_FAIL]"
                   << " seq=" << seq
                   << " stage=cudaPointerGetAttributes";
        return -1;
    }

    if (attr.type != cudaMemoryTypeDevice) {
        LOG(ERROR) << "[INTRA_NVLINK_DIAG][REGISTER_FAIL]"
                   << " seq=" << seq
                   << " stage=memory-type"
                   << " addr=" << addr
                   << " type=" << static_cast<int>(attr.type);
        return -1;
    }

    CUdeviceptr base_ptr = 0;
    size_t alloc_size = 0;
    CUresult cu_err =
        cuMemGetAddressRange(&base_ptr, &alloc_size, (CUdeviceptr)addr);

    LOG(INFO) << "[INTRA_NVLINK_DIAG][EXPORT_ALLOC]"
              << " seq=" << seq
              << " addr=" << addr
              << " attr_device=" << attr.device
              << " cuMemGetAddressRange_rc=" << cuResultToString(cu_err)
              << " base_ptr=0x" << std::hex << static_cast<uint64_t>(base_ptr)
              << std::dec
              << " alloc_size=" << alloc_size
              << " offset_from_base="
              << (cu_err == CUDA_SUCCESS
                      ? static_cast<uint64_t>((CUdeviceptr)addr - base_ptr)
                      : 0);
    if (cu_err != CUDA_SUCCESS) {
        LOG(ERROR) << "[INTRA_NVLINK_DIAG][REGISTER_FAIL]"
                   << " seq=" << seq
                   << " stage=cuMemGetAddressRange"
                   << " rc=" << cuResultToString(cu_err);
        return -1;
    }

    if (registered_base_addrs_.count((uint64_t)base_ptr)) {
        LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_DUPLICATE]"
                  << " seq=" << seq
                  << " base_ptr=0x" << std::hex
                  << static_cast<uint64_t>(base_ptr) << std::dec
                  << " alloc_size=" << alloc_size;
        return 0;
    }

    cudaIpcMemHandle_t handle{};
    logCudaContext("registerLocalMemory:before-ipc-get",
                   reinterpret_cast<void *>(base_ptr), seq);
    err = cudaIpcGetMemHandle(&handle, (void *)base_ptr);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][EXPORT_HANDLE]"
              << " seq=" << seq
              << " base_ptr=0x" << std::hex
              << static_cast<uint64_t>(base_ptr) << std::dec
              << " alloc_size=" << alloc_size
              << " rc=" << static_cast<int>(err)
              << "/" << cudaGetErrorString(err)
              << " handle_sig={"
              << bytesSignature(&handle, sizeof(handle)) << "}";
    logCudaContext("registerLocalMemory:after-ipc-get",
                   reinterpret_cast<void *>(base_ptr), seq);
    if (err != cudaSuccess) {
        LOG(ERROR) << "[INTRA_NVLINK_DIAG][REGISTER_FAIL]"
                   << " seq=" << seq
                   << " stage=cudaIpcGetMemHandle";
        return -1;
    }

    (void)remote_accessible;
    BufferDesc desc;
    desc.addr = (uint64_t)base_ptr;
    desc.length = alloc_size;
    desc.name = location;
    desc.shm_name = serializeBinaryData(&handle, sizeof(cudaIpcMemHandle_t));

    int rc = metadata_->addLocalMemoryBuffer(desc, true);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][METADATA_ADD_BUFFER]"
              << " seq=" << seq
              << " rc=" << rc
              << " base_ptr=0x" << std::hex << desc.addr << std::dec
              << " length=" << desc.length
              << " location=" << desc.name
              << " serialized_handle_bytes=" << desc.shm_name.size();

    if (rc == 0) {
        registered_base_addrs_.insert((uint64_t)base_ptr);
    }

    LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_END]"
              << " seq=" << seq
              << " rc=" << rc
              << " registered_base_count=" << registered_base_addrs_.size();
    return rc;
}

int IntraNodeNvlinkTransport::unregisterLocalMemory(
    void *addr, bool update_metadata) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);

    LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_BEGIN]"
              << " seq=" << seq
              << " addr=" << addr
              << " update_metadata=" << update_metadata;
    logCudaContext("unregisterLocalMemory:begin", addr, seq);

    CUdeviceptr base_ptr = 0;
    size_t alloc_size = 0;
    CUresult cu_err =
        cuMemGetAddressRange(&base_ptr, &alloc_size, (CUdeviceptr)addr);

    void *key_ptr = addr;
    if (cu_err == CUDA_SUCCESS) {
        key_ptr = (void *)base_ptr;
    }

    LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_RANGE]"
              << " seq=" << seq
              << " rc=" << cuResultToString(cu_err)
              << " input_addr=" << addr
              << " resolved_base=0x" << std::hex
              << static_cast<uint64_t>(base_ptr) << std::dec
              << " alloc_size=" << alloc_size
              << " key_ptr=" << key_ptr;

    if (cu_err != CUDA_SUCCESS) {
        LOG(WARNING) << "[INTRA_NVLINK_DIAG][UNREGISTER_RANGE_WARN]"
                     << " seq=" << seq
                     << " addr=" << addr
                     << " rc=" << cuResultToString(cu_err)
                     << " using_input_address_as_key=1";
    }

    {
        std::lock_guard<std::mutex> lock(register_mutex_);
        size_t erased = registered_base_addrs_.erase((uint64_t)key_ptr);
        LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_LOCAL_SET]"
                  << " seq=" << seq
                  << " key_ptr=" << key_ptr
                  << " erased=" << erased
                  << " remaining=" << registered_base_addrs_.size();
    }

    int rc = metadata_->removeLocalMemoryBuffer(key_ptr, update_metadata);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_END]"
              << " seq=" << seq
              << " metadata_rc=" << rc
              << " key_ptr=" << key_ptr;
    logCudaContext("unregisterLocalMemory:end", addr, seq);
    return rc;
}

int IntraNodeNvlinkTransport::relocateSharedMemoryAddress(
    uint64_t &dest_addr, uint64_t length, uint64_t target_id) {
    uint64_t seq = currentDiagSequence();
    if (seq == 0) seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);

    const uint64_t requested_dest = dest_addr;
    LOG(INFO) << "[INTRA_NVLINK_DIAG][RELOCATE_BEGIN]"
              << " seq=" << seq
              << " target_id=" << target_id
              << " requested_dest=0x" << std::hex << requested_dest
              << std::dec
              << " length=" << length
              << " remap_entries=" << remap_entries_.size();
    logCudaContext("relocateSharedMemoryAddress:begin", nullptr, seq);

    auto desc = metadata_->getSegmentDescByID(target_id);
    if (!desc) {
        LOG(ERROR) << "[INTRA_NVLINK_DIAG][RELOCATE_FAIL]"
                   << " seq=" << seq
                   << " stage=getSegmentDescByID"
                   << " target_id=" << target_id
                   << " desc=null";
        return ERR_INVALID_ARGUMENT;
    }

    LOG(INFO) << "[INTRA_NVLINK_DIAG][SEGMENT]"
              << " seq=" << seq
              << " target_id=" << target_id
              << " desc=" << desc.get()
              << " name=" << desc->name
              << " protocol=" << desc->protocol
              << " buffer_count=" << desc->buffers.size();

    int index = 0;
    for (auto &entry : desc->buffers) {
        const bool has_handle = !entry.shm_name.empty();
        const bool range_match =
            has_handle && entry.addr <= requested_dest &&
            requested_dest + length <= entry.addr + entry.length;

        LOG(INFO) << "[INTRA_NVLINK_DIAG][BUFFER_SCAN]"
                  << " seq=" << seq
                  << " target_id=" << target_id
                  << " index=" << index
                  << " remote_base=0x" << std::hex << entry.addr
                  << std::dec
                  << " remote_length=" << entry.length
                  << " requested_dest=0x" << std::hex << requested_dest
                  << std::dec
                  << " request_length=" << length
                  << " has_handle=" << has_handle
                  << " serialized_handle_bytes=" << entry.shm_name.size()
                  << " range_match=" << range_match;

        if (range_match) {
            const auto key = std::make_pair(target_id, entry.addr);
            const uint64_t offset = requested_dest - entry.addr;

            remap_lock_.lockShared();
            auto cached_it = remap_entries_.find(key);
            if (cached_it != remap_entries_.end()) {
                auto shm_addr = cached_it->second.shm_addr;
                auto mapped_length = cached_it->second.length;
                remap_lock_.unlockShared();

                dest_addr = offset + reinterpret_cast<uint64_t>(shm_addr);
                LOG(INFO) << "[INTRA_NVLINK_DIAG][REMAP_CACHE_HIT]"
                          << " seq=" << seq
                          << " target_id=" << target_id
                          << " remote_base=0x" << std::hex << entry.addr
                          << " offset=0x" << offset
                          << " mapped_base=" << shm_addr
                          << " relocated_dest=0x" << dest_addr
                          << std::dec
                          << " mapped_length=" << mapped_length;
                logCudaContext("relocateSharedMemoryAddress:cache-hit",
                               reinterpret_cast<void *>(dest_addr), seq);
                return 0;
            }
            remap_lock_.unlockShared();

            LOG(INFO) << "[INTRA_NVLINK_DIAG][REMAP_CACHE_MISS]"
                      << " seq=" << seq
                      << " target_id=" << target_id
                      << " remote_base=0x" << std::hex << entry.addr
                      << std::dec
                      << " remap_entries_before=" << remap_entries_.size();

            RWSpinlock::WriteGuard lock_guard(remap_lock_);
            if (!remap_entries_.count(key)) {
                std::vector<unsigned char> output_buffer;
                deserializeBinaryData(entry.shm_name, output_buffer);

                LOG(INFO) << "[INTRA_NVLINK_DIAG][HANDLE_DESERIALIZE]"
                          << " seq=" << seq
                          << " target_id=" << target_id
                          << " remote_base=0x" << std::hex << entry.addr
                          << std::dec
                          << " decoded_bytes=" << output_buffer.size()
                          << " expected_bytes=" << sizeof(cudaIpcMemHandle_t);

                if (output_buffer.size() == sizeof(cudaIpcMemHandle_t)) {
                    cudaIpcMemHandle_t handle{};
                    memcpy(&handle, output_buffer.data(), sizeof(handle));

                    LOG(INFO) << "[INTRA_NVLINK_DIAG][IMPORT]"
                              << " seq=" << seq
                              << " target_id=" << target_id
                              << " requested_dest=0x" << std::hex
                              << requested_dest
                              << " remote_base=0x" << entry.addr
                              << " offset=0x" << offset
                              << std::dec
                              << " request_length=" << length
                              << " remote_region_length=" << entry.length
                              << " handle_sig={"
                              << bytesSignature(&handle, sizeof(handle)) << "}";

                    logCudaContext(
                        "relocateSharedMemoryAddress:before-ipc-open",
                        nullptr, seq);

                    void *shm_addr = nullptr;
                    cudaError_t err = cudaIpcOpenMemHandle(
                        &shm_addr, handle, cudaIpcMemLazyEnablePeerAccess);

                    logCudaContext(
                        err == cudaSuccess
                            ? "relocateSharedMemoryAddress:after-ipc-open-success"
                            : "relocateSharedMemoryAddress:after-ipc-open-failure",
                        shm_addr, seq);

                    if (err != cudaSuccess) {
                        LOG(ERROR) << "[INTRA_NVLINK_DIAG][IMPORT_FAIL]"
                                   << " seq=" << seq
                                   << " target_id=" << target_id
                                   << " requested_dest=0x" << std::hex
                                   << requested_dest
                                   << " remote_base=0x" << entry.addr
                                   << std::dec
                                   << " request_length=" << length
                                   << " remote_region_length=" << entry.length
                                   << " err_code=" << static_cast<int>(err)
                                   << " err_string=" << cudaGetErrorString(err)
                                   << " handle_sig={"
                                   << bytesSignature(&handle, sizeof(handle))
                                   << "}";
                        return -1;
                    }

                    LOG(INFO) << "[INTRA_NVLINK_DIAG][IMPORT_SUCCESS]"
                              << " seq=" << seq
                              << " target_id=" << target_id
                              << " remote_base=0x" << std::hex << entry.addr
                              << std::dec
                              << " mapped_base=" << shm_addr
                              << " remote_region_length=" << entry.length
                              << " handle_sig={"
                              << bytesSignature(&handle, sizeof(handle)) << "}";

                    OpenedShmEntry shm_entry;
                    shm_entry.shm_addr = shm_addr;
                    shm_entry.length = entry.length;
                    remap_entries_[key] = shm_entry;

                    LOG(INFO) << "[INTRA_NVLINK_DIAG][REMAP_CACHE_INSERT]"
                              << " seq=" << seq
                              << " target_id=" << target_id
                              << " remote_base=0x" << std::hex << entry.addr
                              << std::dec
                              << " mapped_base=" << shm_addr
                              << " remap_entries_after="
                              << remap_entries_.size();
                } else {
                    LOG(ERROR) << "[INTRA_NVLINK_DIAG][RELOCATE_FAIL]"
                               << " seq=" << seq
                               << " stage=handle-size"
                               << " decoded_bytes=" << output_buffer.size()
                               << " expected_bytes="
                               << sizeof(cudaIpcMemHandle_t)
                               << " target_id=" << target_id
                               << " remote_base=0x" << std::hex << entry.addr
                               << std::dec;
                    return -1;
                }
            } else {
                LOG(INFO) << "[INTRA_NVLINK_DIAG][REMAP_CACHE_RACE_HIT]"
                          << " seq=" << seq
                          << " target_id=" << target_id
                          << " remote_base=0x" << std::hex << entry.addr
                          << std::dec;
            }

            auto shm_addr = remap_entries_[key].shm_addr;
            dest_addr = offset + reinterpret_cast<uint64_t>(shm_addr);

            LOG(INFO) << "[INTRA_NVLINK_DIAG][RELOCATE_SUCCESS]"
                      << " seq=" << seq
                      << " target_id=" << target_id
                      << " remote_base=0x" << std::hex << entry.addr
                      << " requested_dest=0x" << requested_dest
                      << " offset=0x" << offset
                      << " mapped_base=" << shm_addr
                      << " relocated_dest=0x" << dest_addr
                      << std::dec
                      << " request_length=" << length;
            logCudaContext("relocateSharedMemoryAddress:end",
                           reinterpret_cast<void *>(dest_addr), seq);
            return 0;
        }
        ++index;
    }

    LOG(ERROR) << "[INTRA_NVLINK_DIAG][RELOCATE_FAIL]"
               << " seq=" << seq
               << " stage=no-buffer-range"
               << " target_id=" << target_id
               << " requested_dest=0x" << std::hex << requested_dest
               << " requested_end=0x" << (requested_dest + length)
               << std::dec
               << " request_length=" << length
               << " buffer_count=" << desc->buffers.size();
    logCudaContext("relocateSharedMemoryAddress:no-buffer-range", nullptr, seq);
    return ERR_INVALID_ARGUMENT;
}

int IntraNodeNvlinkTransport::registerLocalMemoryBatch(
    const std::vector<Transport::BufferEntry> &buffer_list,
    const std::string &location) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_BATCH_BEGIN]"
              << " seq=" << seq
              << " buffer_count=" << buffer_list.size()
              << " location=" << location;
    size_t index = 0;
    for (auto &buffer : buffer_list) {
        LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_BATCH_ITEM]"
                  << " seq=" << seq
                  << " index=" << index
                  << " addr=" << buffer.addr
                  << " length=" << buffer.length;
        int rc =
            registerLocalMemory(buffer.addr, buffer.length, location, true, false);
        if (rc != 0) {
            LOG(ERROR) << "[INTRA_NVLINK_DIAG][REGISTER_BATCH_ITEM_FAIL]"
                       << " seq=" << seq
                       << " index=" << index
                       << " rc=" << rc;
        }
        ++index;
    }
    int rc = metadata_->updateLocalSegmentDesc();
    LOG(INFO) << "[INTRA_NVLINK_DIAG][REGISTER_BATCH_END]"
              << " seq=" << seq
              << " metadata_update_rc=" << rc;
    return rc;
}

int IntraNodeNvlinkTransport::unregisterLocalMemoryBatch(
    const std::vector<void *> &addr_list) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_BATCH_BEGIN]"
              << " seq=" << seq
              << " addr_count=" << addr_list.size();
    size_t index = 0;
    for (auto &addr : addr_list) {
        LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_BATCH_ITEM]"
                  << " seq=" << seq
                  << " index=" << index
                  << " addr=" << addr;
        int rc = unregisterLocalMemory(addr, false);
        if (rc != 0) {
            LOG(ERROR) << "[INTRA_NVLINK_DIAG][UNREGISTER_BATCH_ITEM_FAIL]"
                       << " seq=" << seq
                       << " index=" << index
                       << " rc=" << rc;
        }
        ++index;
    }
    int rc = metadata_->updateLocalSegmentDesc();
    LOG(INFO) << "[INTRA_NVLINK_DIAG][UNREGISTER_BATCH_END]"
              << " seq=" << seq
              << " metadata_update_rc=" << rc;
    return rc;
}

void *IntraNodeNvlinkTransport::allocatePinnedLocalMemory(size_t size) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][ALLOCATE_BEGIN]"
              << " seq=" << seq
              << " size=" << size;
    logCudaContext("allocatePinnedLocalMemory:before-cudaMalloc", nullptr, seq);

    void *ptr = nullptr;
    cudaError_t res = cudaMalloc(&ptr, size);

    LOG(INFO) << "[INTRA_NVLINK_DIAG][ALLOCATE_END]"
              << " seq=" << seq
              << " size=" << size
              << " ptr=" << ptr
              << " rc=" << static_cast<int>(res)
              << "/" << cudaGetErrorString(res)
              << " ptr_attrs={" << pointerAttributesToString(ptr) << "}";
    logCudaContext("allocatePinnedLocalMemory:after-cudaMalloc", ptr, seq);

    if (res == cudaSuccess) {
        return ptr;
    }
    return nullptr;
}

void IntraNodeNvlinkTransport::freePinnedLocalMemory(void *ptr) {
    const uint64_t seq = nextDiagSequence();
    ScopedDiagSequence diag_scope(seq);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][FREE_BEGIN]"
              << " seq=" << seq
              << " ptr=" << ptr
              << " ptr_attrs={" << pointerAttributesToString(ptr) << "}";
    logCudaContext("freePinnedLocalMemory:before-cudaFree", ptr, seq);
    cudaError_t rc = cudaFree(ptr);
    LOG(INFO) << "[INTRA_NVLINK_DIAG][FREE_END]"
              << " seq=" << seq
              << " ptr=" << ptr
              << " rc=" << static_cast<int>(rc)
              << "/" << cudaGetErrorString(rc);
    logCudaContext("freePinnedLocalMemory:after-cudaFree", nullptr, seq);
}

}  // namespace mooncake
