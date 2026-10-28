"""함수 안 import 를 새로 더하고 warmup 목록에 안 넣으면 빨강이 되게 한다(#189, 배경 #177).

`config/warmup.py` 의 HEAVY_LAZY_MODULES 는 async 경로가 함수 안에서 늦게 부르는 무거운 모듈을 워커 시작 때
미리 불러 두는 목록이다. 손으로 맞추면 새 함수 안 import 가 누락돼 첫 요청 루프 정지가 조용히 되살아난다.
`apps/` 의 비시험 코드에서 무거운 패키지(아래 HEAVY_ROOTS)를 함수 안에서 불러오는 import 를 AST 로 모으고,
새 프로세스에서 `config.asgi` 만 불러온 뒤 그 모듈이 모두 `sys.modules` 에 올라와 있음을 단언한다.
(별도 프로세스라 다른 시험이 먼저 불러 둔 모듈에 오염되지 않는다.) 대상을 무거운 패키지로 좁혀 오탐을 막는다.
누락되면 `config/warmup.py` 의 HEAVY_LAZY_MODULES 에 그 모듈을 더한다. 확인 수단: scripts/e2e-soak (루프 지연 >200ms 스택).
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# 함수 안에서 늦게 불러오면 루프를 수백 ms 붙잡는 무거운 패키지의 최상위 이름
HEAVY_ROOTS = {
    "langchain", "langchain_core", "langchain_openai", "langchain_anthropic",
    "langgraph", "openai", "anthropic", "taskiq", "aiohttp", "psycopg", "psycopg_pool",
}


def _function_level_heavy_imports() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted((BACKEND / "apps").rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if "tests" in rel.parts or "migrations" in rel.parts:
            continue
        for fn in ast.walk(ast.parse(path.read_text())):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                for name in names:
                    if name.split(".")[0] in HEAVY_ROOTS:
                        found.setdefault(name, []).append(f"{rel}:{node.lineno}")
    return found


def test_함수_안에서_불러오는_무거운_모듈은_워커_시작_때_이미_올라와_있다(asgi_loaded_modules: frozenset[str]) -> None:
    lazy = _function_level_heavy_imports()
    assert lazy, "AST 수집이 비었다 — 수집 로직이 깨졌다"
    missing = sorted(m for m in lazy if m not in asgi_loaded_modules)
    assert missing == [], (
        "함수 안 import 인데 워커 시작 때 안 불러온다 — config/warmup.py HEAVY_LAZY_MODULES 에 더하라(#177/#189): "
        + ", ".join(f"{m} ({', '.join(lazy[m])})" for m in missing)
    )
