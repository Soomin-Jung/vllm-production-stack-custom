#!/usr/bin/env python3
"""
Analyze Mooncake nvlink_intra NVDBG logs for Issue #6.

Input is intended to be a complete text capture such as:

  kubectl logs deploy/<deployment> --all-containers --prefix > pd-cell.log
  python3 analyze_nvlink_nvdbg.py pd-cell.log

The analyzer is deliberately conservative:
- it distinguishes "not seen in supplied log" from a proven absence;
- it correlates IPC open failures with the nearest transfer abort and peer EXPORT;
- it does not require third-party Python packages;
- it prints a compact evidence block suitable for pasting into Issue #6/chat.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import re
import sys
from pathlib import Path
from typing import Iterable, Optional


PREFIX_RE = re.compile(
    r"^\[pod/(?P<pod>[^/]+)/(?P<container>[^\]]+)\]\s*(?P<msg>.*)$"
)
TAG_RE = re.compile(r"\[NVDBG\]\[(?P<tag>[A-Z_]+)\]")
FIELD_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")

DECODE_PULL_FAIL_RE = re.compile(
    r"pulling kv_caches.*failed.*Mooncake transfer engine returned\s+-1",
    re.IGNORECASE,
)
GUARDIAN_RE = re.compile(r"\[pd-cell-guardian\]")
PD_GPU_RE = re.compile(
    r"\[pd-gpu\]\s+(?P<key>reserved-cvd|selected-indices|selected-uuids)=(?P<value>\S+)"
)

TAGS = (
    "INSTALL",
    "EXPORT",
    "IPC_OPEN_BEGIN",
    "IPC_OPEN_OK",
    "IPC_OPEN_FAIL",
    "TRANSFER_ABORT",
    "COPY_FAIL",
    "UNREGISTER",
    "DTOR",
    "RANGE_MISS",
    "REMAP_HIT",
)

TAG_MEANING = {
    "INSTALL": "Mooncake nvlink_intra transport/segment generation 시작",
    "EXPORT": "local CUDA allocation을 IPC handle로 export 성공",
    "IPC_OPEN_BEGIN": "remote CUDA allocation을 cudaIpcOpenMemHandle() 하기 직전",
    "IPC_OPEN_OK": "remote CUDA IPC mapping 성공",
    "IPC_OPEN_FAIL": "cudaIpcOpenMemHandle() 실패",
    "TRANSFER_ABORT": "IPC relocation 실패 때문에 해당 transfer request 중단",
    "COPY_FAIL": "IPC open 이후 실제 cudaMemcpy() 단계 실패",
    "UNREGISTER": "local exported allocation metadata 해제",
    "DTOR": "IntraNodeNvlinkTransport 종료/IPC mapping 정리",
    "RANGE_MISS": "remote metadata에서 requested address를 포함하는 buffer를 못 찾음",
    "REMAP_HIT": "이미 open한 IPC mapping cache 재사용(trace 활성 시만 출력)",
}


@dataclasses.dataclass
class Event:
    line_no: int
    raw: str
    msg: str
    pod: str
    container: str
    tag: str
    fields: dict[str, str]


@dataclasses.dataclass
class Finding:
    name: str
    level: str
    detail: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyze Mooncake nvlink_intra NVDBG logs."
    )
    p.add_argument("logfile", type=Path, help="kubectl logs output text file")
    p.add_argument(
        "--max-failures",
        type=int,
        default=5,
        help="maximum IPC_OPEN_FAIL cases to print in detail (default: 5)",
    )
    p.add_argument(
        "--explain",
        action="store_true",
        help="print a short explanation of each NVDBG section",
    )
    return p.parse_args()


def split_prefix(line: str) -> tuple[str, str, str]:
    m = PREFIX_RE.match(line)
    if not m:
        return "<unknown-pod>", "<unknown-container>", line
    return m.group("pod"), m.group("container"), m.group("msg")


def parse_fields(msg: str) -> dict[str, str]:
    fields = {m.group(1): m.group(2) for m in FIELD_RE.finditer(msg)}

    # err_string can contain spaces; stop before the next stable field.
    m = re.search(r"\berr_string=(.*?)\s+segment=", msg)
    if m:
        fields["err_string"] = m.group(1).strip()

    return fields


def parse_log(lines: Iterable[str]) -> tuple[
    list[Event],
    list[tuple[int, str]],
    list[tuple[int, str]],
    dict[tuple[str, str], dict[str, str]],
]:
    events: list[Event] = []
    decode_failures: list[tuple[int, str]] = []
    guardian_lines: list[tuple[int, str]] = []
    gpu_maps: dict[tuple[str, str], dict[str, str]] = collections.defaultdict(dict)

    for line_no, raw_line in enumerate(lines, 1):
        raw = raw_line.rstrip("\n")
        pod, container, msg = split_prefix(raw)

        if DECODE_PULL_FAIL_RE.search(msg):
            decode_failures.append((line_no, raw))
        if GUARDIAN_RE.search(msg):
            guardian_lines.append((line_no, raw))

        gm = PD_GPU_RE.search(msg)
        if gm:
            gpu_maps[(pod, container)][gm.group("key")] = gm.group("value")

        tm = TAG_RE.search(msg)
        if not tm:
            continue

        events.append(
            Event(
                line_no=line_no,
                raw=raw,
                msg=msg,
                pod=pod,
                container=container,
                tag=tm.group("tag"),
                fields=parse_fields(msg),
            )
        )

    return events, decode_failures, guardian_lines, dict(gpu_maps)


def int_field(event: Optional[Event], key: str) -> Optional[int]:
    if event is None:
        return None
    value = event.fields.get(key)
    if value is None:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def is_null_ctx(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.lower() in {"0", "0x0", "(nil)", "nullptr", "null"}


def same_scope(a: Event, b: Event) -> bool:
    if a.pod != "<unknown-pod>" and b.pod != "<unknown-pod>" and a.pod != b.pod:
        return False
    if (
        a.container != "<unknown-container>"
        and b.container != "<unknown-container>"
        and a.container != b.container
    ):
        return False
    return True


def match_fields(a: Event, b: Event, keys: tuple[str, ...]) -> bool:
    compared = 0
    for key in keys:
        av = a.fields.get(key)
        bv = b.fields.get(key)
        if av is None or bv is None:
            continue
        compared += 1
        if av != bv:
            return False
    return compared > 0


def nearest_before(
    events: list[Event],
    target: Event,
    tag: str,
    max_lines: int = 40,
) -> Optional[Event]:
    candidates = [
        e
        for e in events
        if e.tag == tag
        and e.line_no < target.line_no
        and target.line_no - e.line_no <= max_lines
        and same_scope(e, target)
    ]
    if not candidates:
        return None

    # Prefer exact IPC identity when fields exist.
    exact = [
        e
        for e in candidates
        if match_fields(e, target, ("target_id", "remote_base", "handle_sig"))
    ]
    if exact:
        return max(exact, key=lambda e: e.line_no)
    return max(candidates, key=lambda e: e.line_no)


def nearest_after(
    events: list[Event],
    target: Event,
    tag: str,
    max_lines: int = 40,
) -> Optional[Event]:
    candidates = [
        e
        for e in events
        if e.tag == tag
        and e.line_no > target.line_no
        and e.line_no - target.line_no <= max_lines
        and same_scope(e, target)
    ]
    if not candidates:
        return None

    exact = [
        e
        for e in candidates
        if match_fields(e, target, ("target_id",))
    ]
    if exact:
        return min(exact, key=lambda e: e.line_no)
    return min(candidates, key=lambda e: e.line_no)


def matching_exports(events: list[Event], fail: Event) -> list[tuple[Event, str]]:
    sig = fail.fields.get("handle_sig")
    base = fail.fields.get("remote_base")
    matches: list[tuple[Event, str]] = []

    for e in events:
        if e.tag != "EXPORT":
            continue

        sig_match = sig is not None and e.fields.get("handle_sig") == sig
        base_match = base is not None and e.fields.get("base") == base

        if sig_match and base_match:
            kind = "handle+base"
        elif sig_match:
            kind = "handle-only"
        elif base_match:
            kind = "base-only"
        else:
            continue

        # Prefer same Pod generation when --prefix information exists.
        if (
            fail.pod != "<unknown-pod>"
            and e.pod != "<unknown-pod>"
            and fail.pod != e.pod
        ):
            kind += "/other-pod"

        matches.append((e, kind))

    matches.sort(
        key=lambda item: (
            0 if item[1] == "handle+base" else 1,
            0 if item[0].pod == fail.pod else 1,
            abs(item[0].line_no - fail.line_no),
        )
    )
    return matches


def matching_unregisters(
    events: list[Event],
    fail: Event,
    export: Optional[Event],
) -> list[Event]:
    if export is None:
        return []

    base = export.fields.get("base")
    if base is None:
        return []

    out = []
    for e in events:
        if e.tag != "UNREGISTER" or e.line_no >= fail.line_no:
            continue
        if e.pod != export.pod or e.container != export.container:
            continue
        if e.fields.get("base") == base:
            out.append(e)
    return out


def classify_fail(
    events: list[Event],
    fail: Event,
    begin: Optional[Event],
    abort: Optional[Event],
    exports: list[tuple[Event, str]],
) -> list[Finding]:
    findings: list[Finding] = []

    ctx = fail.fields.get("ctx")
    ctx_rc = int_field(fail, "ctx_rc")
    ctx_dev = int_field(fail, "ctx_device")
    dev_rc = int_field(fail, "dev_rc")
    src_dev = int_field(abort, "source_device")
    ptr_rc = int_field(abort, "ptr_rc")
    source_ptr_ctx = abort.fields.get("source_ptr_ctx") if abort else None
    source_ptr_ctx_rc = int_field(abort, "source_ptr_ctx_rc")

    # H1: importer worker has no usable current CUcontext.
    if is_null_ctx(ctx) or (ctx_rc is not None and ctx_rc != 0) or (dev_rc is not None and dev_rc != 0):
        findings.append(
            Finding(
                "Importer current CUDA context",
                "HIGH",
                f"IPC open failure 시 ctx={ctx} ctx_rc={ctx_rc} "
                f"ctx_device={ctx_dev} dev_rc={dev_rc}. "
                "Mooncake worker thread의 current CUcontext가 없거나 유효하지 않은 정황.",
            )
        )
    elif ctx is not None and ctx_dev is not None:
        findings.append(
            Finding(
                "Importer current CUDA context",
                "NOT_SEEN",
                f"실패 시 current context는 존재: ctx={ctx}, ctx_device={ctx_dev}, "
                f"ctx_rc={ctx_rc}, dev_rc={dev_rc}.",
            )
        )
    else:
        findings.append(
            Finding(
                "Importer current CUDA context",
                "UNKNOWN",
                "필요한 ctx/ctx_device 필드가 로그에 충분하지 않음.",
            )
        )

    # H2: worker active device differs from source pointer device.
    if abort is not None and ptr_rc == 0 and src_dev is not None and ctx_dev is not None:
        if src_dev != ctx_dev:
            findings.append(
                Finding(
                    "Worker ctx_device vs source_device",
                    "HIGH",
                    f"ctx_device={ctx_dev}, source_device={src_dev}. "
                    "worker thread device binding 불일치. Mooncake per-device context/stream "
                    "초기화 문제와 강하게 부합.",
                )
            )
        else:
            findings.append(
                Finding(
                    "Worker ctx_device vs source_device",
                    "NOT_SEEN",
                    f"ctx_device={ctx_dev} == source_device={src_dev}.",
                )
            )
    elif abort is not None and ptr_rc not in (None, 0):
        findings.append(
            Finding(
                "Worker ctx_device vs source_device",
                "UNKNOWN",
                f"실패 후 source pointer attribute 조회 자체가 실패(ptr_rc={ptr_rc}).",
            )
        )
    else:
        findings.append(
            Finding(
                "Worker ctx_device vs source_device",
                "UNKNOWN",
                "matching TRANSFER_ABORT/source_device 정보를 찾지 못함.",
            )
        )

    # H2.5: same device ordinal can still mean a different CUcontext.
    if abort is not None and source_ptr_ctx is not None:
        if source_ptr_ctx_rc not in (None, 0):
            findings.append(
                Finding(
                    "Current ctx vs source pointer owning ctx",
                    "UNKNOWN",
                    f"CU_POINTER_ATTRIBUTE_CONTEXT query failed "
                    f"(source_ptr_ctx_rc={source_ptr_ctx_rc}).",
                )
            )
        elif ctx is not None and source_ptr_ctx != ctx:
            findings.append(
                Finding(
                    "Current ctx vs source pointer owning ctx",
                    "HIGH",
                    f"IPC_OPEN_FAIL current_ctx={ctx}, "
                    f"source_ptr_ctx={source_ptr_ctx}. "
                    "같은 device ordinal이어도 서로 다른 CUcontext. "
                    "CUDA IPC/peer mapping context restriction과 강하게 부합.",
                )
            )
        elif ctx is not None:
            findings.append(
                Finding(
                    "Current ctx vs source pointer owning ctx",
                    "NOT_SEEN",
                    f"current_ctx == source_ptr_ctx == {ctx}.",
                )
            )
        else:
            findings.append(
                Finding(
                    "Current ctx vs source pointer owning ctx",
                    "UNKNOWN",
                    f"source_ptr_ctx={source_ptr_ctx}, current ctx 정보 부족.",
                )
            )
    else:
        findings.append(
            Finding(
                "Current ctx vs source pointer owning ctx",
                "UNKNOWN",
                "새 debug build의 source_ptr_ctx 필드가 없음.",
            )
        )

    # H3: importer metadata/handle corresponds to an exporter in the supplied log.
    if exports:
        best, kind = exports[0]
        if kind == "handle+base":
            findings.append(
                Finding(
                    "Remote IPC export/metadata match",
                    "NOT_SEEN",
                    f"동일 handle_sig + remote_base의 peer EXPORT 확인: "
                    f"{best.container} line {best.line_no}.",
                )
            )
        else:
            findings.append(
                Finding(
                    "Remote IPC export/metadata match",
                    "MEDIUM",
                    f"부분 일치만 확인({kind}): {best.container} line {best.line_no}. "
                    "handle/base metadata 조합을 재확인할 가치가 있음.",
                )
            )
    else:
        total_exports = sum(1 for e in events if e.tag == "EXPORT")
        level = "MEDIUM" if total_exports else "UNKNOWN"
        findings.append(
            Finding(
                "Remote IPC export/metadata match",
                level,
                "실패 remote_base/handle_sig와 일치하는 EXPORT를 제공된 로그에서 찾지 못함"
                + (
                    f" (다른 EXPORT {total_exports}건은 존재). stale/foreign metadata 후보."
                    if total_exports
                    else ". exporter startup 로그가 파일에 포함됐는지 먼저 확인."
                ),
            )
        )

    # H4: exporter allocation was unregistered before importer tried to open it.
    best_export = exports[0][0] if exports else None
    unregisters = matching_unregisters(events, fail, best_export)
    if unregisters:
        last = unregisters[-1]
        findings.append(
            Finding(
                "Exporter allocation lifecycle",
                "HIGH",
                f"matching EXPORT base가 IPC open 전에 UNREGISTER됨: "
                f"{last.container} line {last.line_no}. stale handle 가능성.",
            )
        )
    elif best_export is not None:
        findings.append(
            Finding(
                "Exporter allocation lifecycle",
                "NOT_SEEN",
                "matching exporter allocation의 선행 UNREGISTER는 제공된 로그에서 보이지 않음.",
            )
        )
    else:
        findings.append(
            Finding(
                "Exporter allocation lifecycle",
                "UNKNOWN",
                "matching exporter를 찾지 못해 lifecycle 상관분석 불가.",
            )
        )

    # H5: begin/fail context changed inside the single cudaIpcOpenMemHandle boundary.
    if begin is not None:
        begin_ctx = begin.fields.get("ctx")
        begin_dev = int_field(begin, "ctx_device")
        if begin_ctx != ctx or begin_dev != ctx_dev:
            findings.append(
                Finding(
                    "Context changed across IPC open boundary",
                    "HIGH",
                    f"BEGIN ctx={begin_ctx}/dev={begin_dev} -> "
                    f"FAIL ctx={ctx}/dev={ctx_dev}.",
                )
            )
        else:
            findings.append(
                Finding(
                    "Context changed across IPC open boundary",
                    "NOT_SEEN",
                    f"BEGIN/FAIL context 동일: ctx={ctx}, device={ctx_dev}.",
                )
            )
    else:
        findings.append(
            Finding(
                "Context changed across IPC open boundary",
                "UNKNOWN",
                "matching IPC_OPEN_BEGIN을 찾지 못함.",
            )
        )

    return findings


def thread_context_summary(events: list[Event]) -> list[str]:
    opens = [e for e in events if e.tag in {"IPC_OPEN_OK", "IPC_OPEN_FAIL"}]
    grouped: dict[tuple[str, str, str], dict[str, set[str]]] = {}

    for e in opens:
        pid = e.fields.get("pid", "?")
        key = (e.pod, e.container, pid)
        g = grouped.setdefault(
            key,
            {"ok_ctx": set(), "fail_ctx": set(), "ok_threads": set(), "fail_threads": set()},
        )
        sig = f"ctx={e.fields.get('ctx','?')}/dev={e.fields.get('ctx_device','?')}"
        tid = e.fields.get("tid", "?")
        if e.tag == "IPC_OPEN_OK":
            g["ok_ctx"].add(sig)
            g["ok_threads"].add(tid)
        else:
            g["fail_ctx"].add(sig)
            g["fail_threads"].add(tid)

    notes = []
    for (pod, container, pid), g in sorted(grouped.items()):
        if g["ok_ctx"] and g["fail_ctx"]:
            notes.append(
                f"{pod}/{container} pid={pid}: 같은 process에서 IPC_OPEN_OK와 FAIL 모두 존재; "
                f"OK contexts={sorted(g['ok_ctx'])}, FAIL contexts={sorted(g['fail_ctx'])}, "
                f"OK tids={sorted(g['ok_threads'])}, FAIL tids={sorted(g['fail_threads'])}"
            )
    return notes


def format_scope(e: Event) -> str:
    if e.pod == "<unknown-pod>" and e.container == "<unknown-container>":
        return "unknown"
    return f"{e.pod}/{e.container}"


def compact_raw(e: Optional[Event]) -> Optional[str]:
    if e is None:
        return None
    return f"L{e.line_no} {e.raw}"


def logical_to_uuid(
    gpu_maps: dict[tuple[str, str], dict[str, str]],
    event: Optional[Event],
    logical_device: Optional[int],
) -> Optional[str]:
    if event is None or logical_device is None:
        return None
    mapping = gpu_maps.get((event.pod, event.container), {})
    cvd = mapping.get("reserved-cvd")
    if not cvd:
        return None
    uuids = cvd.split(",")
    if logical_device < 0 or logical_device >= len(uuids):
        return None
    return uuids[logical_device]


def print_gpu_mapping(
    gpu_maps: dict[tuple[str, str], dict[str, str]],
    events: list[Event],
) -> None:
    print("\n=== 1.5 GPU RESERVATION / PHYSICAL PAIRING ===")
    if not gpu_maps:
        print(
            "[pd-gpu] reserved-cvd/selected-uuids 로그를 찾지 못했습니다. "
            "Pod startup부터 전체 로그를 포함하면 GOOD/BAD physical GPU pair를 비교할 수 있습니다."
        )
        return

    for (pod, container), m in sorted(gpu_maps.items()):
        print(
            f"- {pod}/{container}: "
            f"reserved-cvd={m.get('reserved-cvd','?')} "
            f"selected-indices={m.get('selected-indices','?')} "
            f"selected-uuids={m.get('selected-uuids','?')}"
        )

    opens = [e for e in events if e.tag in {"IPC_OPEN_OK", "IPC_OPEN_FAIL"}]
    if not opens:
        return

    print("- IPC open physical-pair correlation:")
    shown: set[tuple[str, str, str, str, str]] = set()
    for e in opens:
        src_dev = int_field(e, "ctx_device")
        src_uuid = logical_to_uuid(gpu_maps, e, src_dev)
        exports = matching_exports(events, e)
        peer = exports[0][0] if exports else None
        peer_dev = int_field(peer, "device") if peer else None
        peer_uuid = logical_to_uuid(gpu_maps, peer, peer_dev) if peer else None
        key = (
            e.tag,
            e.container,
            str(src_dev),
            str(peer_dev),
            f"{src_uuid}->{peer_uuid}",
        )
        if key in shown:
            continue
        shown.add(key)
        print(
            f"  {e.tag}: {e.container} logical={src_dev} physical={src_uuid or '?'} "
            f"-> {(peer.container if peer else 'peer?')} logical={peer_dev} "
            f"physical={peer_uuid or '?'}"
        )


def print_counts(events: list[Event], decode_failures: list[tuple[int, str]]) -> None:
    print("\n=== 1. LOG PROFILE ===")
    if not events:
        print("NVDBG event를 찾지 못했습니다.")
        return

    by_scope: dict[tuple[str, str], collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for e in events:
        by_scope[(e.pod, e.container)][e.tag] += 1

    for (pod, container), counts in sorted(by_scope.items()):
        useful = ", ".join(f"{tag}={counts[tag]}" for tag in TAGS if counts[tag])
        print(f"- {pod}/{container}: {useful}")

    if decode_failures:
        print(f"- vLLM Decode pull failure lines: {len(decode_failures)}")


def print_failures(
    events: list[Event],
    gpu_maps: dict[tuple[str, str], dict[str, str]],
    max_failures: int,
) -> None:
    fails = [e for e in events if e.tag == "IPC_OPEN_FAIL"]

    print("\n=== 2. IPC OPEN FAILURE ANALYSIS ===")
    if not fails:
        oks = [e for e in events if e.tag == "IPC_OPEN_OK"]
        print(f"IPC_OPEN_FAIL 없음. IPC_OPEN_OK={len(oks)}")
        return

    for idx, fail in enumerate(fails[:max_failures], 1):
        begin = nearest_before(events, fail, "IPC_OPEN_BEGIN")
        abort = nearest_after(events, fail, "TRANSFER_ABORT")
        exports = matching_exports(events, fail)
        findings = classify_fail(events, fail, begin, abort, exports)

        print(f"\n--- Failure #{idx}: {format_scope(fail)} line {fail.line_no} ---")
        fail_ctx_dev = int_field(fail, "ctx_device")
        fail_src_uuid = logical_to_uuid(gpu_maps, fail, fail_ctx_dev)
        print(
            "raw: "
            f"err_code={fail.fields.get('err_code','?')} "
            f"err_string={fail.fields.get('err_string','?')} "
            f"ctx={fail.fields.get('ctx','?')} "
            f"ctx_device={fail.fields.get('ctx_device','?')} "
            f"ctx_physical_uuid={fail_src_uuid or '?'} "
            f"source_ptr_ctx={(abort.fields.get('source_ptr_ctx','?') if abort else '?')} "
            f"target_id={fail.fields.get('target_id','?')} "
            f"remote_base={fail.fields.get('remote_base','?')} "
            f"handle_sig={fail.fields.get('handle_sig','?')}"
        )

        for f in findings:
            print(f"[{f.level:8}] {f.name}: {f.detail}")

        if exports:
            peer, kind = exports[0]
            peer_dev = int_field(peer, "device")
            peer_uuid = logical_to_uuid(gpu_maps, peer, peer_dev)
            print(
                f"[MATCH   ] peer EXPORT ({kind}): "
                f"{format_scope(peer)} line {peer.line_no}, "
                f"device={peer.fields.get('device','?')} "
                f"physical_uuid={peer_uuid or '?'} "
                f"ctx_device={peer.fields.get('ctx_device','?')} "
                f"base={peer.fields.get('base','?')} "
                f"handle_sig={peer.fields.get('handle_sig','?')}"
            )

    if len(fails) > max_failures:
        print(f"\n... {len(fails) - max_failures} additional IPC_OPEN_FAIL cases omitted.")


def print_global_findings(events: list[Event]) -> None:
    print("\n=== 3. CROSS-EVENT CLUES ===")

    thread_notes = thread_context_summary(events)
    if thread_notes:
        print("[HIGH/MEDIUM] 동일 process에서 성공/실패가 섞임:")
        for note in thread_notes:
            print(f"  - {note}")
        print(
            "  -> static Helm/GPU assignment 문제보다 thread-local CUDA context/device "
            "초기화 차이를 우선 확인할 가치가 큼."
        )
    else:
        print("- 동일 process 내 IPC_OPEN_OK/FAIL 혼재 패턴은 제공된 로그에서 확인되지 않음.")

    copy_fails = [e for e in events if e.tag == "COPY_FAIL"]
    range_misses = [e for e in events if e.tag == "RANGE_MISS"]
    dtors = [e for e in events if e.tag == "DTOR"]
    unregisters = [e for e in events if e.tag == "UNREGISTER"]

    print(
        f"- COPY_FAIL={len(copy_fails)}: "
        + (
            "IPC import 이후 copy 단계 문제도 존재."
            if copy_fails
            else "현재 실패는 copy 이전 IPC-open 경계에 더 가깝다."
        )
    )
    print(
        f"- RANGE_MISS={len(range_misses)}: "
        + (
            "remote metadata address-range mismatch가 존재."
            if range_misses
            else "metadata range lookup 실패는 보이지 않음."
        )
    )
    print(f"- UNREGISTER={len(unregisters)}, DTOR={len(dtors)}")


def print_evidence(
    events: list[Event],
    decode_failures: list[tuple[int, str]],
    guardian_lines: list[tuple[int, str]],
    max_failures: int,
) -> None:
    print("\n=== 4. MINIMAL EVIDENCE BLOCK (이 부분만 복사해도 됨) ===")

    printed: set[int] = set()

    def emit_event(e: Optional[Event]) -> None:
        if e is None or e.line_no in printed:
            return
        printed.add(e.line_no)
        print(f"L{e.line_no} {e.raw}")

    # One INSTALL per container is enough to identify process generation.
    installs_by_scope: dict[tuple[str, str], Event] = {}
    for e in events:
        if e.tag == "INSTALL":
            installs_by_scope.setdefault((e.pod, e.container), e)
    for e in installs_by_scope.values():
        emit_event(e)

    fails = [e for e in events if e.tag == "IPC_OPEN_FAIL"][:max_failures]
    for fail in fails:
        begin = nearest_before(events, fail, "IPC_OPEN_BEGIN")
        abort = nearest_after(events, fail, "TRANSFER_ABORT")
        exports = matching_exports(events, fail)

        emit_event(begin)
        emit_event(fail)
        emit_event(abort)
        if exports:
            emit_event(exports[0][0])

    # Only exceptional later-stage events.
    for e in events:
        if e.tag in {"COPY_FAIL", "RANGE_MISS", "UNREGISTER", "DTOR"}:
            emit_event(e)

    # Include at most two Decode-side symptoms and guardian lifecycle lines.
    for line_no, raw in decode_failures[:2]:
        if line_no not in printed:
            printed.add(line_no)
            print(f"L{line_no} {raw}")

    for line_no, raw in guardian_lines[-4:]:
        if line_no not in printed:
            printed.add(line_no)
            print(f"L{line_no} {raw}")


def print_explanation() -> None:
    print("\n=== NVDBG SECTION MEANING ===")
    for tag in TAGS:
        print(f"- {tag:15} {TAG_MEANING[tag]}")


def main() -> int:
    args = parse_args()
    if not args.logfile.exists():
        print(f"ERROR: file not found: {args.logfile}", file=sys.stderr)
        return 2

    try:
        text = args.logfile.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: cannot read {args.logfile}: {exc}", file=sys.stderr)
        return 2

    events, decode_failures, guardian_lines, gpu_maps = parse_log(text.splitlines(True))

    print("Mooncake nvlink_intra NVDBG analyzer")
    print(f"input={args.logfile}")
    print(f"lines={len(text.splitlines())} nvdbg_events={len(events)}")

    print_counts(events, decode_failures)
    print_gpu_mapping(gpu_maps, events)
    print_failures(events, gpu_maps, max(1, args.max_failures))
    print_global_findings(events)
    print_evidence(
        events,
        decode_failures,
        guardian_lines,
        max(1, args.max_failures),
    )

    if args.explain:
        print_explanation()

    if not events:
        print(
            "\nTIP: debug build 로그에 [NVDBG]가 있는지 확인하세요. "
            "kubectl logs ... --all-containers --prefix 전체를 저장하는 것을 권장합니다."
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
