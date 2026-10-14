"""Static determinism enforcement scanner shared across experiment projects."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

RUNTIME_SURFACE_ROOTS = (
    "engine",
    "scripts",
    "tools",
    "runtime/artifacts/manifests/jobs",
    "experiments",
)

FORBIDDEN_CALLS = {
    "random.random", "random.randint", "random.randrange", "random.choice", "random.choices",
    "random.shuffle", "random.uniform", "random.gauss", "random.normalvariate", "random.triangular",
    "random.seed",
    "numpy.random.seed", "numpy.random.default_rng", "numpy.random.Generator", "numpy.random.PCG64",
    "numpy.random.MT19937",
    "torch.manual_seed", "torch.seed", "torch.initial_seed", "torch.rand", "torch.rand_like",
    "torch.randn", "torch.randint", "torch.randperm", "torch.bernoulli", "torch.normal",
    "torch.poisson", "torch.multinomial", "torch.Generator", "torch.distributions.*",
    "os.urandom", "secrets.*", "uuid.uuid4",
    "builtins.eval", "builtins.exec", "importlib.import_module", "builtins.__import__",
}

REGEX_PATTERNS = (
    r"\bnp\.random\.", r"\bnumpy\.random\.", r"\brandom\.",
    r"\btorch\.(rand_like|randn?|randint|randperm|bernoulli|normal|poisson|multinomial|manual_seed|seed|initial_seed|Generator)\b",
    r"\btorch\.distributions\.\w+\b", r"\bos\.urandom\b", r"\bsecrets\.\w+\b", r"\buuid\.uuid4\b",
    r"\bimportlib\.import_module\b", r"\bgetattr\s*\(", r"\b(eval|exec)\s*\(",
)

ALLOWLIST_PATH = Path("governance/determinism_allowlist.json")
ALLOWLIST_LOCK_PATH = Path("governance/determinism_allowlist.lock.json")


@dataclass(frozen=True)
class ScanHit:
    code: str
    file: str
    line: int
    col: int
    symbol: str
    context: str
    allowlisted: bool
    allowlist_entry_id: Optional[str] = None


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _relpath(repo_root: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def _iter_runtime_surface_files(repo_root: Path) -> Iterable[Path]:
    for root in RUNTIME_SURFACE_ROOTS:
        base = repo_root / root
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            rp = _relpath(repo_root, p)
            if "/docs/" in rp or rp.startswith("docs/"):
                continue
            if "/site/" in rp or rp.startswith("site/"):
                continue
            if "/static/" in rp and rp.startswith("infra/webui/static/"):
                continue
            yield p


def _validate_allowlist_schema(obj: dict) -> List[str]:
    errs: List[str] = []
    if not isinstance(obj, dict):
        return ["allowlist root is not an object"]
    if (obj.get("schema_version") or "") != "determinism_allowlist_v1":
        errs.append("schema_version must be determinism_allowlist_v1")
    entries = obj.get("entries", None)
    if entries is None or not isinstance(entries, list):
        errs.append("entries must be a list")
        return errs
    for i, ent in enumerate(entries):
        if not isinstance(ent, dict):
            errs.append(f"entries[{i}] is not an object")
            continue
        if not str(ent.get("id") or "").strip():
            errs.append(f"entries[{i}].id is required")
        for req in ("justification", "reviewer", "removal_phase"):
            if not str(ent.get(req) or "").strip():
                errs.append(f"entries[{i}].{req} is required")
        scope = ent.get("scope", None)
        if not isinstance(scope, dict):
            errs.append(f"entries[{i}].scope must be an object")
            continue
        st = str(scope.get("type") or "").strip()
        if st not in ("module", "file", "api"):
            errs.append(f"entries[{i}].scope.type must be one of module,file,api")
        paths = scope.get("paths", None)
        if not isinstance(paths, list) or not paths:
            errs.append(f"entries[{i}].scope.paths must be a non-empty list")
        symbols = ent.get("symbols", None)
        if st in ("file", "api"):
            if not isinstance(symbols, list) or not symbols:
                errs.append(f"entries[{i}].symbols must be a non-empty list for scope.type={st}")
        elif symbols is not None and not isinstance(symbols, list):
            errs.append(f"entries[{i}].symbols must be a list when present")
    return errs


def _load_allowlist(repo_root: Path) -> Tuple[dict, str]:
    p = repo_root / ALLOWLIST_PATH
    if not p.exists():
        obj = {"schema_version": "determinism_allowlist_v1", "entries": []}
        return obj, _sha256_bytes(json.dumps(obj, sort_keys=True).encode("utf-8"))
    raw = p.read_bytes()
    allow_sha = _sha256_bytes(raw)
    obj = json.loads(raw.decode("utf-8"))
    errs = _validate_allowlist_schema(obj)
    if errs:
        raise ValueError("allowlist schema invalid: " + "; ".join(errs[:8]))
    return obj, allow_sha


def _load_allowlist_lock(repo_root: Path) -> Optional[dict]:
    p = repo_root / ALLOWLIST_LOCK_PATH
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _glob_match(path: str, pat: str) -> bool:
    pat = pat.replace("\\", "/")
    path = path.replace("\\", "/")
    rx = re.escape(pat).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(rx, path) is not None


def _symbol_match(symbol: str, pat: str) -> bool:
    if pat.endswith(".*"):
        return symbol.startswith(pat[:-2] + ".")
    return symbol == pat


def _match_allowlist(repo_root: Path, allowlist: dict, file_rel: str, symbol: str) -> Optional[str]:
    entries = allowlist.get("entries", []) or []
    for ent in entries:
        ent_id = str(ent.get("id") or "").strip()
        scope = ent.get("scope", {}) or {}
        scope_type = str(scope.get("type") or "").strip()
        paths = scope.get("paths", []) or []
        symbols = ent.get("symbols", []) or []
        if not ent_id or not scope_type:
            continue
        if scope_type == "module":
            for pat in paths:
                if _glob_match(file_rel, str(pat)):
                    return ent_id
        if scope_type in ("file", "api"):
            for pat in paths:
                if _glob_match(file_rel, str(pat)):
                    for s in symbols:
                        if _symbol_match(symbol, str(s)):
                            return ent_id
    return None


def _resolve_call_symbol(func: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    if isinstance(func, ast.Name):
        name = func.id
        if name in ("eval", "exec"):
            return f"builtins.{name}"
        if name == "__import__":
            return "builtins.__import__"
        return aliases.get(name, name)
    if isinstance(func, ast.Attribute):
        parts: List[str] = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        parts = list(reversed(parts))
        if not parts:
            return None
        head_full = aliases.get(parts[0], parts[0])
        if "." in head_full:
            parts = head_full.split(".") + parts[1:]
        else:
            parts[0] = head_full
        if parts[0] == "np":
            parts[0] = "numpy"
        return ".".join(parts)
    return None


def _resolve_value_symbol(val: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    if isinstance(val, ast.Name):
        return aliases.get(val.id, val.id)
    if isinstance(val, ast.Attribute):
        parts: List[str] = []
        cur: ast.AST = val
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        parts = list(reversed(parts))
        if not parts:
            return None
        head_full = aliases.get(parts[0], parts[0])
        if "." in head_full:
            parts = head_full.split(".") + parts[1:]
        else:
            parts[0] = head_full
        if parts[0] == "np":
            parts[0] = "numpy"
        return ".".join(parts)
    return None


def _call_getattr_targets_forbidden_rng(node: ast.Call, aliases: Dict[str, str]) -> bool:
    try:
        if len(node.args) < 2:
            return False
        tgt_sym = _resolve_value_symbol(node.args[0], aliases)
        if not tgt_sym:
            return False
        attr = node.args[1]
        attr_name = attr.value if isinstance(attr, ast.Constant) and isinstance(attr.value, str) else None
        rng_targets = ("numpy.random", "random", "torch", "secrets", "uuid", "os")
        if not any(tgt_sym == p or tgt_sym.startswith(p + ".") for p in rng_targets):
            return False
        if attr_name is None:
            return True
        forbidden_leafs = {
            "random", "randint", "randrange", "choice", "choices", "shuffle", "uniform", "gauss",
            "normalvariate", "triangular", "seed", "default_rng", "Generator", "PCG64", "MT19937",
            "manual_seed", "initial_seed", "rand", "randn", "randperm", "bernoulli", "normal",
            "urandom", "uuid4",
        }
        return str(attr_name) in forbidden_leafs
    except Exception:
        return False


def _call_import_module_targets_forbidden_rng(node: ast.Call) -> bool:
    try:
        if not node.args:
            return False
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            return arg0.value.strip() in {"random", "numpy", "numpy.random", "torch", "secrets", "uuid", "os"}
    except Exception:
        return False
    return False


def _call___import___targets_forbidden_rng(node: ast.Call) -> bool:
    return _call_import_module_targets_forbidden_rng(node)


class _RNGVisitor(ast.NodeVisitor):
    def __init__(self, file_rel: str, src_lines: List[str]):
        self.file_rel = file_rel
        self.src_lines = src_lines
        self.hits: List[Tuple[str, int, int, str]] = []
        self.aliases: Dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for n in node.names:
            self.aliases[n.asname or n.name.split(".")[-1]] = n.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for n in node.names:
            self.aliases[n.asname or n.name] = f"{mod}.{n.name}" if mod else n.name
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        sym = _resolve_call_symbol(node.func, self.aliases)
        if sym:
            if sym in ("builtins.eval", "builtins.exec"):
                self._add(sym, node)
            elif sym == "getattr":
                if _call_getattr_targets_forbidden_rng(node, self.aliases):
                    self._add("getattr", node)
            elif sym == "importlib.import_module":
                if _call_import_module_targets_forbidden_rng(node):
                    self._add("importlib.import_module", node)
            elif sym == "builtins.__import__":
                if _call___import___targets_forbidden_rng(node):
                    self._add("builtins.__import__", node)
            else:
                for f in FORBIDDEN_CALLS:
                    if f.endswith(".*"):
                        if sym.startswith(f[:-2] + "."):
                            self._add(sym, node)
                            break
                    elif sym == f:
                        self._add(sym, node)
                        break
        self.generic_visit(node)

    def _add(self, symbol: str, node: ast.AST) -> None:
        line = int(getattr(node, "lineno", 1) or 1)
        col = int(getattr(node, "col_offset", 0) or 0)
        ctx = self.src_lines[line - 1].rstrip("\n")[:240] if 1 <= line <= len(self.src_lines) else ""
        self.hits.append((symbol, line, col, ctx))


def _code_for_symbol(symbol: str) -> str:
    if symbol in ("builtins.eval", "builtins.exec", "importlib.import_module", "builtins.__import__", "getattr"):
        return "DET_DYNAMIC_RNG_DISPATCH"
    if symbol.endswith("seed") or symbol.endswith("manual_seed"):
        return "DET_FORBIDDEN_SEED_CALL"
    return "DET_FORBIDDEN_RNG_API"


def _format_hit(h: ScanHit) -> str:
    return (
        "[DETERMINISM_VIOLATION]\n"
        f"code={h.code}\nfile={h.file}\nline={h.line}\ncol={h.col}\n"
        f"symbol={h.symbol}\ncontext={h.context}\n"
    )


def scan_repo(repo_root: Path) -> Tuple[List[ScanHit], dict]:
    allowlist_schema_ok = True
    allowlist_schema_error = ""
    try:
        allowlist, allow_sha = _load_allowlist(repo_root)
    except Exception as e:
        allowlist_schema_ok = False
        allowlist_schema_error = str(e)
        allowlist = {"schema_version": "determinism_allowlist_v1", "entries": []}
        allow_sha = _sha256_bytes(json.dumps(allowlist, sort_keys=True).encode("utf-8"))
    lock = _load_allowlist_lock(repo_root)
    hits: List[ScanHit] = []
    for p in _iter_runtime_surface_files(repo_root):
        file_rel = _relpath(repo_root, p)
        txt = _read_text(p)
        lines = txt.splitlines(True)
        parsed_ok = True
        try:
            tree = ast.parse(txt, filename=file_rel)
        except SyntaxError:
            parsed_ok = False
            tree = None
        if tree is not None:
            v = _RNGVisitor(file_rel=file_rel, src_lines=[l.rstrip("\n") for l in lines])
            v.visit(tree)
            for sym, line, col, ctx in v.hits:
                ent_id = _match_allowlist(repo_root, allowlist, file_rel, sym)
                hits.append(ScanHit(code=_code_for_symbol(sym), file=file_rel, line=line, col=col, symbol=sym, context=ctx, allowlisted=ent_id is not None, allowlist_entry_id=ent_id))
        if not parsed_ok:
            for rx in REGEX_PATTERNS:
                for m in re.finditer(rx, txt):
                    line = txt[: m.start()].count("\n") + 1
                    col = m.start() - (txt.rfind("\n", 0, m.start()) + 1)
                    ctx = lines[line - 1].rstrip("\n")[:240] if 1 <= line <= len(lines) else ""
                    sym = f"regex:{rx}"
                    ent_id = _match_allowlist(repo_root, allowlist, file_rel, sym)
                    hits.append(ScanHit(code="DET_AST_PARSE_FAILED", file=file_rel, line=line, col=col, symbol=sym, context=ctx, allowlisted=ent_id is not None, allowlist_entry_id=ent_id))
    violations = [h for h in hits if not h.allowlisted]
    allowlisted_hits = [h for h in hits if h.allowlisted]
    lock_ok = True
    lock_reason = ""
    if lock is None:
        lock_ok = False
        lock_reason = "allowlist lock missing"
    else:
        if str(lock.get("allowlist_sha256") or "") != allow_sha:
            lock_ok = False
            lock_reason = "allowlist sha mismatch vs lock"
        else:
            expected_total = int(lock.get("total_allowlisted_hits", 0) or 0)
            actual_total = len(allowlisted_hits)
            if expected_total != actual_total:
                lock_ok = False
                lock_reason = f"allowlist usage count changed: expected={expected_total} actual={actual_total}"
            else:
                by_entry = lock.get("by_entry", {}) or {}
                if by_entry:
                    actual_by: Dict[str, int] = {}
                    for h in allowlisted_hits:
                        k = str(h.allowlist_entry_id or "")
                        if k:
                            actual_by[k] = actual_by.get(k, 0) + 1
                    for k, exp_v in by_entry.items():
                        av = int(actual_by.get(str(k), 0) or 0)
                        if int(exp_v or 0) != av:
                            lock_ok = False
                            lock_reason = f"allowlist entry usage changed: entry={k} expected={int(exp_v or 0)} actual={av}"
                            break
    summary = {
        "schema_version": "determinism_scan_report_v1",
        "repo_root": str(repo_root.resolve()),
        "runtime_surface_roots": list(RUNTIME_SURFACE_ROOTS),
        "allowlist_path": str(ALLOWLIST_PATH),
        "allowlist_sha256": allow_sha,
        "lock_path": str(ALLOWLIST_LOCK_PATH),
        "lock_ok": lock_ok,
        "lock_reason": lock_reason,
        "allowlist_schema_ok": allowlist_schema_ok,
        "allowlist_schema_error": allowlist_schema_error,
        "hits_total": len(hits),
        "violations_total": len(violations),
        "allowlisted_total": len(allowlisted_hits),
    }
    return hits, summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo_root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write_report", default="")
    args = ap.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    hits, summary = scan_repo(repo_root)
    violations = [h for h in hits if not h.allowlisted]
    allowlisted_hits = [h for h in hits if h.allowlisted]
    report = {"summary": summary, "violations": [h.__dict__ for h in violations], "allowlisted_hits": [h.__dict__ for h in allowlisted_hits]}
    if args.write_report:
        Path(args.write_report).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        if not summary.get("lock_ok", False):
            print(
                "[DETERMINISM_ALLOWLIST_LOCK]\n"
                f"reason={summary.get('lock_reason','')}\n"
                f"allowlist={summary.get('allowlist_path')}\n"
                f"lock={summary.get('lock_path')}\n",
                file=sys.stderr,
            )
        for h in violations:
            print(_format_hit(h), file=sys.stderr)
    if not summary.get("lock_ok", False):
        return 3
    if violations:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
