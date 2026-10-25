"""e2e 부하 재현 판 폴더를 읽어 느린 요청을 요약한다(#177). 표준 라이브러리만 쓴다.

    python3 scripts/e2e-soak/analyze.py <판 폴더>              # 판 하나 자세히
    python3 scripts/e2e-soak/analyze.py --table <판 폴더>...   # 여러 판을 표 한 장으로
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

SLOW_MS = 2000


class Req(TypedDict):
    t: float
    pid: int
    m: str
    p: str
    s: int
    ttfb: int | None
    ms: int
    inf: int
    infmax: int
    sse: bool


@dataclass
class Run:
    name: str
    reqs: list[Req]
    lags: list[dict[str, float]]
    stacks: list[dict[str, object]]
    passed: int
    failed: list[str]
    skipped: int
    exit_line: str
    stats: list[tuple[float, str, float]]
    pg: list[tuple[float, int, int, int]]
    tasks: list[tuple[float, str, float]]  # (끝난 시각, 작업 이름, 걸린 초)


_ID = re.compile(r"/[0-9a-f]{8}-[0-9a-f-]{27,}|/\d+(?=/|$)")


def norm(path: str) -> str:
    return _ID.sub("/<id>", path)


def _load_pw(run: Path) -> tuple[int, list[str], int]:
    f = run / "pw.json"
    if not f.exists():
        return 0, ["(pw.json 없음)"], 0
    data = json.loads(f.read_text())
    passed, skipped, failed = 0, 0, []

    def walk(suite: dict[str, object], trail: str) -> None:
        nonlocal passed, skipped
        title = str(suite.get("title", ""))
        here = f"{trail} › {title}" if trail and title else (title or trail)
        for spec in suite.get("specs", []):  # type: ignore[union-attr]
            for test in spec["tests"]:
                status = test.get("status")
                if status == "expected":
                    passed += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    last = test["results"][-1] if test["results"] else {}
                    failed.append(f"{here} › {spec['title']} ({status}, {last.get('duration', '?')}ms)")
        for child in suite.get("suites", []):  # type: ignore[union-attr]
            walk(child, here)

    for s in data.get("suites", []):
        walk(s, "")
    return passed, failed, skipped


def _ts(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso[:26].rstrip("Z") + "+00:00").timestamp()


_TASK = re.compile(r"^(\S+) .*Task ([\w.]+)\[[^\]]+\] succeeded in ([\d.]+)s")


def load(run: Path) -> Run:
    reqs: list[Req] = []
    lags: list[dict[str, float]] = []
    stacks: list[dict[str, object]] = []
    for line in (run / "api.log").read_text(errors="replace").splitlines():
        i = line.find("SOAK {")
        if i < 0:
            continue
        rec = json.loads(line[i + 5:])
        if rec["k"] == "req":
            reqs.append(rec)
        elif rec["k"] == "lag":
            lags.append(rec)
        else:
            stacks.append(rec)
    passed, failed, skipped = _load_pw(run)
    stats: list[tuple[float, str, float]] = []
    if (run / "stats.tsv").exists():
        for line in (run / "stats.tsv").read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2].endswith("%"):
                stats.append((float(parts[0]), parts[1], float(parts[2][:-1])))
    pg: list[tuple[float, int, int, int]] = []
    if (run / "pg.tsv").exists():
        for line in (run / "pg.tsv").read_text().splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                pg.append((float(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])))
    tasks: list[tuple[float, str, float]] = []
    for name in ("worker.log", "chat-worker.log"):
        f = run / name
        if f.exists():
            for line in f.read_text(errors="replace").splitlines():
                m = _TASK.search(line.split("| ", 1)[-1])
                if m:
                    tasks.append((_ts(m.group(1)), m.group(2), float(m.group(3))))
    exit_line = (run / "exit.txt").read_text().strip() if (run / "exit.txt").exists() else "?"
    return Run(run.name, reqs, lags, stacks, passed, failed, skipped, exit_line, stats, pg, tasks)


def latency(r: Req) -> int:
    """SSE 는 머리까지(ttfb), 나머지는 전체 시간."""
    return (r["ttfb"] or 0) if r["sse"] else r["ms"]


def slow(run: Run) -> list[Req]:
    return sorted((r for r in run.reqs if latency(r) > SLOW_MS), key=latency, reverse=True)


def cpu_at(run: Run, t: float, window: float = 3.0) -> str:
    near = [(n, c) for (ts, n, c) in run.stats if abs(ts - t) <= window and c >= 50]
    best: dict[str, float] = {}
    for n, c in near:
        short = n.replace("sandcorp-flow-test-", "").rsplit("-", 1)[0]
        best[short] = max(best.get(short, 0), c)
    return ",".join(f"{k}:{v:.0f}%" for k, v in sorted(best.items(), key=lambda kv: -kv[1])[:4]) or "-"


def lag_near(run: Run, r: Req) -> int:
    start = r["t"] - latency(r) / 1000
    return max((int(lg["ms"]) for lg in run.lags if lg["pid"] == r["pid"] and start <= lg["t"] <= r["t"] + 0.2), default=0)


def detail(run: Run) -> None:
    normal = [r for r in run.reqs if not r["sse"]]
    print(f"# {run.name}  {run.exit_line}  통과 {run.passed} / 실패 {len(run.failed)} / skip {run.skipped}")
    for f in run.failed:
        print(f"  실패: {f}")
    lat = sorted(latency(r) for r in normal)
    if lat:
        pct = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))]  # noqa: E731
        print(f"요청 {len(run.reqs)}(SSE {len(run.reqs) - len(normal)})  p50 {pct(.5)}ms p95 {pct(.95)}ms p99 {pct(.99)}ms 최대 {lat[-1]}ms")
    lag_ms = [int(lg["ms"]) for lg in run.lags]
    print(f"이벤트 루프 지연(>200ms) {len(lag_ms)}회, 최대 {max(lag_ms, default=0)}ms, 합 {sum(lag_ms)}ms")
    tops: dict[str, int] = {}
    for st in run.stacks:
        frames = [f for f in st["stack"] if "/asyncio/" not in f]  # type: ignore[union-attr]
        key = " ← ".join(reversed(frames[-3:]))
        tops[key] = tops.get(key, 0) + 1
    for key, n in sorted(tops.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  루프를 막은 자리 ×{n}: {key}")
    if run.pg:
        print(f"DB: 최대 active {max(p[1] for p in run.pg)}, 최대 잠금 대기 {max(p[2] for p in run.pg)}, 최대 연결 {max(p[3] for p in run.pg)}")
    by_task: dict[str, list[float]] = {}
    for _, n, d in run.tasks:
        by_task.setdefault(n, []).append(d)
    for n, ds in sorted(by_task.items(), key=lambda kv: -max(kv[1]))[:5]:
        print(f"작업 {n}: {len(ds)}회, 최대 {max(ds):.1f}s")
    s = slow(run)
    print(f"느린 요청(>{SLOW_MS}ms) {len(s)}건")
    for r in s[:25]:
        print(f"  {latency(r):>6}ms {r['m']:<6} {norm(r['p']):<55} s={r['s']} pid={r['pid']} inf={r['inf']} infmax={r['infmax']}"
              f" lag={lag_near(run, r)}ms cpu[{cpu_at(run, r['t'] - latency(r) / 2000)}]")
    eps: dict[str, int] = {}
    for r in s:
        eps[f"{r['m']} {norm(r['p'])}"] = eps.get(f"{r['m']} {norm(r['p'])}", 0) + 1
    if eps:
        print("느린 엔드포인트: " + ", ".join(f"{k}×{v}" for k, v in sorted(eps.items(), key=lambda kv: -kv[1])[:8]))


def table(runs: list[Run]) -> None:
    print("| 판 | 결과 | 통과/실패/skip | 요청 | >2s | 최대 ms(SSE 머리 포함) | p99 ms | 루프 지연>200ms(최대) |")
    print("|---|---|---|---|---|---|---|---|")
    for run in runs:
        lat = sorted(latency(r) for r in run.reqs if not r["sse"]) or [0]
        p99 = lat[min(len(lat) - 1, int(len(lat) * 0.99))]
        worst = max([latency(r) for r in run.reqs] or [0])
        lag_ms = [int(lg["ms"]) for lg in run.lags]
        print(f"| {run.name} | {run.exit_line} | {run.passed}/{len(run.failed)}/{run.skipped} | {len(run.reqs)} | {len(slow(run))}"
              f" | {worst} | {p99} | {len(lag_ms)}({max(lag_ms, default=0)}) |")


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--table":
        table([load(Path(p)) for p in argv[1:] if (Path(p) / "api.log").exists()])
    else:
        detail(load(Path(argv[0])))


if __name__ == "__main__":
    main(sys.argv[1:])
