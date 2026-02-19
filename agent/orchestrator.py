import json
import os
import re
import subprocess
from typing import Any, Dict, List, Tuple
import shlex
import hashlib
import glob
import time

from agent.stacks.registry import resolve_stack_spec, load_catalog
from jsonschema import validate

from agent.tools.github_tools import (
    get_issue,
    ensure_branch,
    git_commit_all,
    git_push,
    git_commits_ahead,
    gh_pr_ensure,
)
from agent.tools.llm import chat_json
from agent.tools.patch_apply import apply_patch_object
from agent.tools.repo_introspect import list_files, snapshot

# Use ONLY failure_hints module (no local override)
from agent.tools.failure_hints import (
    classify_failure as fh_classify_failure,
    summarize_hints,
    should_count_as_stuck,
)

try:
    from agent.tools.pinecone_memory import query as pine_query, upsert as pine_upsert
except Exception:
    pine_query = None
    pine_upsert = None

BASE_DIR = os.path.dirname(__file__)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


RUN_SCHEMA = load_json(os.path.join(BASE_DIR, "schemas", "run_request.schema.json"))
PLAN_SCHEMA = load_json(os.path.join(BASE_DIR, "schemas", "plan.schema.json"))
PATCH_SCHEMA = load_json(os.path.join(BASE_DIR, "schemas", "patch.schema.json"))
TEST_SCHEMA = load_json(os.path.join(BASE_DIR, "schemas", "test_report.schema.json"))


def safe_validate(obj: Dict[str, Any], schema: Dict[str, Any], schema_name: str) -> None:
    try:
        validate(instance=obj, schema=schema)
    except Exception as e:
        msg = getattr(e, "message", str(e))
        raise ValueError(f"JSON inválido para {schema_name}: {msg}") from e


def extract_json_from_comment(body: str) -> Dict[str, Any]:
    m = re.search(r"/agent\s+run\s*(\{.*\})\s*$", (body or "").strip(), re.DOTALL)
    if not m:
        m = re.search(r"(\{.*\})", (body or "").strip(), re.DOTALL)
    if not m:
        raise ValueError("No se encontró JSON. Usa: /agent run { ... }")
    return json.loads(m.group(1))


def _stringify_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(v)


def _coerce_test_strategy(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if "unit_tests" in v and isinstance(v["unit_tests"], dict):
            ut = v["unit_tests"]
            desc = ut.get("description") or ""
            cmd = ut.get("test_command") or ""
            parts = []
            if desc:
                parts.append(str(desc).strip())
            if cmd:
                parts.append(f"Command sugerido: {cmd}")
            if parts:
                return " | ".join(parts)
        desc = v.get("description") or v.get("strategy") or v.get("details") or ""
        cmd = v.get("test_command") or v.get("command") or ""
        if desc or cmd:
            return " | ".join([p for p in [str(desc).strip(), (f"Command sugerido: {cmd}" if cmd else "")] if p])
    return _stringify_value(v)


def _mk_task_id(i: int, title: str, desc: str) -> str:
    base = f"{i}:{title}:{desc}"
    h = hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"T{i:02d}-{h}"


def _repair_tasks(tasks_val: Any) -> List[Dict[str, str]]:
    if tasks_val is None:
        return []
    if isinstance(tasks_val, dict):
        tasks = [tasks_val]
    elif isinstance(tasks_val, list):
        tasks = tasks_val
    else:
        tasks = [{"description": _stringify_value(tasks_val)}]

    repaired: List[Dict[str, str]] = []
    for idx, t in enumerate(tasks, start=1):
        if not isinstance(t, dict):
            desc = _stringify_value(t)
            title = (desc.split(".")[0] or desc[:60]).strip() or f"Tarea {idx}"
            repaired.append({"id": _mk_task_id(idx, title, desc), "title": title, "description": desc})
            continue

        desc = _stringify_value(t.get("description") or t.get("details") or t.get("what") or "")
        title = _stringify_value(t.get("title") or t.get("name") or "")
        tid = _stringify_value(t.get("id") or "")

        extras = {k: v for k, v in t.items() if k not in ("id", "title", "description", "details", "what", "name")}
        if extras:
            extras_txt = _stringify_value(extras)
            if desc:
                desc = f"{desc}\n\nExtra:\n{extras_txt}"
            else:
                desc = f"Extra:\n{extras_txt}"

        if not title:
            title = (desc.splitlines()[0].split(".")[0] if desc else "").strip()
            if not title:
                title = f"Tarea {idx}"
            if len(title) > 80:
                title = title[:80].rstrip()

        if not desc:
            desc = title

        if not tid or len(tid) < 3:
            tid = _mk_task_id(idx, title, desc)

        if len(tid) < 3:
            tid = _mk_task_id(idx, title, desc)

        repaired.append({"id": tid, "title": title, "description": desc})

    return repaired


def normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    allowed_props = (PLAN_SCHEMA.get("properties") or {})
    allowed_keys = set(allowed_props.keys())

    out: Dict[str, Any] = {k: v for k, v in plan.items() if k in allowed_keys}

    if "test_strategy" in out:
        out["test_strategy"] = _coerce_test_strategy(out["test_strategy"])

    if "tasks" in out:
        out["tasks"] = _repair_tasks(out["tasks"])

    for key, spec in allowed_props.items():
        if key not in out:
            continue
        expected_type = spec.get("type")

        if expected_type == "string" and not isinstance(out[key], str):
            out[key] = _stringify_value(out[key])

        if expected_type == "array":
            items = spec.get("items") or {}
            item_type = items.get("type")
            if item_type == "string":
                v = out[key]
                if isinstance(v, str):
                    out[key] = [v]
                elif isinstance(v, list):
                    out[key] = [x if isinstance(x, str) else _stringify_value(x) for x in v]
                else:
                    out[key] = [_stringify_value(v)]

    return out


def normalize_patch(p: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(p, dict) and "patch" in p and isinstance(p["patch"], dict):
        p = p["patch"]

    out: Dict[str, Any] = {}

    notes = p.get("notes", [])
    if not isinstance(notes, list):
        notes = []
    out["notes"] = [str(x) for x in notes if isinstance(x, (str, int, float, bool))]

    cleaned_files: Dict[str, Any] = {}
    files = p.get("files")
    if isinstance(files, dict):
        for path, val in files.items():
            if not isinstance(path, str) or not path.strip():
                continue

            if isinstance(val, str):
                cleaned_files[path] = val
                continue

            if isinstance(val, dict):
                content = val.get("content")
                op = val.get("operation") or "modify"
                if op not in ("add", "modify", "delete"):
                    op = "modify"

                # delete op: allow missing content
                if op == "delete":
                    cleaned_files[path] = {"operation": "delete", "content": val.get("content") or ""}
                    continue

                if not isinstance(content, str):
                    continue
                cleaned_files[path] = {"operation": op, "content": content}

    if cleaned_files:
        out["files"] = cleaned_files

    patches_in = p.get("patches")
    cleaned_patches: List[Dict[str, str]] = []
    if isinstance(patches_in, list):
        for item in patches_in:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            diff = item.get("diff")
            if isinstance(path, str) and path.strip() and isinstance(diff, str) and len(diff) >= 10:
                cleaned_patches.append({"path": path, "diff": diff})

    if cleaned_patches:
        out["patches"] = cleaned_patches

    return out


def normalize_test_report(tr: Dict[str, Any], run_req: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(tr, dict) and "test_report" in tr and isinstance(tr["test_report"], dict):
        tr = tr["test_report"]

    passed = bool(tr.get("passed", False))
    summary = str(tr.get("summary") or "")[:4000]

    fh = tr.get("failure_hints", [])
    if not isinstance(fh, list):
        fh = []
    fh = [str(x) for x in fh if isinstance(x, (str, int, float, bool))][:50]

    normalized_ac: List[Dict[str, Any]] = []
    ac_items = tr.get("acceptance_criteria_status")

    if isinstance(ac_items, list) and ac_items:
        for item in ac_items:
            if not isinstance(item, dict):
                continue
            crit = item.get("criterion", None)
            if crit is None:
                crit = item.get("criteria", None)
            if crit is None:
                continue

            met = item.get("met", False)
            if isinstance(met, str):
                met = met.strip().lower() in ("true", "1", "yes", "y", "si", "sí")
            else:
                met = bool(met)

            evidence = str(item.get("evidence") or "")[:1200]
            normalized_ac.append({"criterion": str(crit), "met": met, "evidence": evidence})

    if not normalized_ac:
        ac = run_req.get("acceptance_criteria", []) or []
        normalized_ac = [{"criterion": str(c), "met": passed, "evidence": summary[:400]} for c in ac]

    out: Dict[str, Any] = {
        "passed": passed,
        "summary": summary,
        "failure_hints": fh,
        "acceptance_criteria_status": normalized_ac,
    }

    rp = tr.get("recommended_patch", None)
    if isinstance(rp, dict):
        rp_norm = normalize_patch(rp)

        if "patches" in rp_norm and (not isinstance(rp_norm["patches"], list) or len(rp_norm["patches"]) == 0):
            rp_norm.pop("patches", None)

        notes = rp_norm.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        rp_norm["notes"] = [str(x) for x in notes if isinstance(x, (str, int, float, bool))][:50]

        has_files = isinstance(rp_norm.get("files"), dict) and len(rp_norm.get("files")) > 0
        has_patches = isinstance(rp_norm.get("patches"), list) and len(rp_norm.get("patches")) > 0

        if has_files or has_patches:
            out["recommended_patch"] = rp_norm

    return out


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _is_test_path(p: str) -> bool:
    p = p.replace("\\", "/").lower()
    return (
        "/test/" in p
        or p.endswith("_test.go")
        or p.endswith(".spec.ts")
        or p.endswith(".spec.js")
        or p.endswith(".test.ts")
        or p.endswith(".test.js")
        or "/__tests__/" in p
        or "/tests/" in p
    )


def _read_file_safe(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def detect_financial_expected_antipattern(changed_files: List[str]) -> Dict[str, Any]:
    """
    Policy (enterprise): en tests financieros NO se permite hardcodear expected "manual/derivado"
    si no es un golden vector documentado o expected derivado por fórmula/helper.

    Heurística simple y efectiva (multi-stack):
    - Solo mira archivos de test cambiados.
    - Busca variables típicas expected + literal numérico + marcador humano ("manual", "derivado", "known", "valores conocidos").
    """
    markers = [
        r"c[aá]lculo\s+manual",
        r"derivad[oa]",
        r"valores?\s+conocid[oa]s",
        r"known\s+value",
        r"hand\s*calc",
        r"manual\s+calc",
    ]

    # variables expected típicas para finanzas/amortización
    expected_vars = [
        r"cuota_esperada",
        r"pago_esperado",
        r"monthly_payment_expected",
        r"expected_monthly_payment",
        r"expectedPayment",
        r"expected",
    ]

    # asignación con número (int/float) en varios lenguajes
    assign_number = r"(?:=|:)\s*\d+(?:\.\d+)?"

    # comentario (py/js/java/c#) con marker humano
    comment_with_marker = r"(?:#|//|/\*)\s*(?:" + "|".join(markers) + r")"

    # patrón combinado: var expected + = número + comentario marker
    pattern = re.compile(
        r"(?is)\b(" + "|".join(expected_vars) + r")\b\s*" + assign_number + r".{0,80}?" + comment_with_marker
    )

    suspects: List[Dict[str, Any]] = []
    for p in changed_files or []:
        p2 = p.replace("\\", "/")
        if not _is_test_path(p2):
            continue
        if not os.path.isfile(p2):
            continue
        txt = _read_file_safe(p2)
        m = pattern.search(txt)
        if m:
            suspects.append({
                "path": p2,
                "var": m.group(1),
                "match_excerpt": txt[max(0, m.start()-120): m.end()+120],
            })

    return {"violations": suspects, "breaking": bool(suspects)}


def _glob_many(patterns: List[str]) -> List[str]:
    out: List[str] = []
    for pat in patterns:
        out.extend(glob.glob(pat, recursive=True))
    # unique, stable
    seen = set()
    uniq = []
    for p in out:
        if os.path.isfile(p) and p not in seen:
            seen.add(p)
            uniq.append(p)
    return sorted(uniq)


def _exists_any_glob(patterns: List[str]) -> bool:
    for pat in patterns or []:
        try:
            if _glob_many([pat]):
                return True
        except Exception:
            continue
    return False

def _catalog_stacks_view(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """
    Soporta ambos formatos:
      A) mapping plano (tu caso): { "python-django": {...}, "java-spring-boot": {...} }
      B) wrapper: { "stacks": { ... } }
    """
    if not isinstance(catalog, dict):
        return {}
    stacks = catalog.get("stacks")
    if isinstance(stacks, dict):
        return stacks
    return catalog


def _catalog_entry(catalog: Dict[str, Any], stack_id: str) -> Dict[str, Any]:
    stacks = _catalog_stacks_view(catalog)
    entry = stacks.get(stack_id) if isinstance(stacks, dict) else None
    return entry if isinstance(entry, dict) else {}


def _markers_found_for_stack(catalog: Dict[str, Any], stack_id: str) -> bool:
    entry = _catalog_entry(catalog, stack_id)
    markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
    any_of = markers.get("any_of") or []
    if not isinstance(any_of, list) or not any_of:
        return False
    return _exists_any_glob(any_of)


def _repo_has_any_stack_markers(catalog: Dict[str, Any]) -> bool:
    stacks = _catalog_stacks_view(catalog)
    if not isinstance(stacks, dict):
        return False
    for _sid, entry in stacks.items():
        if not isinstance(entry, dict):
            continue
        markers = entry.get("markers") if isinstance(entry.get("markers"), dict) else {}
        any_of = markers.get("any_of") or []
        if isinstance(any_of, list) and any_of and _exists_any_glob(any_of):
            return True
    return False


def _bootstrap_kind_for_stack(catalog: Dict[str, Any], stack_id: str) -> str:
    entry = _catalog_entry(catalog, stack_id)
    bootstrap = entry.get("bootstrap") if isinstance(entry.get("bootstrap"), dict) else {}
    return str(bootstrap.get("kind") or "none").strip().lower()


def _snapshot_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


# -------- Extractors (heurísticos, multi-stack) --------

def _extract_java_public_symbols() -> List[Dict[str, Any]]:
    files = _glob_many(["src/main/java/**/*.java"])
    syms: List[Dict[str, Any]] = []

    pkg_re = re.compile(r"(?m)^\s*package\s+([a-zA-Z0-9_.]+)\s*;")
    class_re = re.compile(r"(?m)^\s*public\s+(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)")
    iface_re = re.compile(r"(?m)^\s*public\s+interface\s+([A-Za-z_]\w*)")
    enum_re  = re.compile(r"(?m)^\s*public\s+enum\s+([A-Za-z_]\w*)")

    # Captura también nombres de params (clave para years↔months)
    method_re = re.compile(
        r"(?m)^\s*public\s+(?:static\s+)?(?:final\s+)?([A-Za-z_]\w*(?:<[^>]+>)?)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*\{?"
    )

    for fp in files:
        if _is_test_path(fp):
            continue
        txt = _read_text(fp)

        pkg = ""
        m = pkg_re.search(txt)
        if m:
            pkg = m.group(1).strip()

        def qname(x: str) -> str:
            return f"{pkg}.{x}" if pkg else x

        for m in class_re.finditer(txt):
            syms.append({"kind": "java.class", "name": qname(m.group(1)), "path": fp})

        for m in iface_re.finditer(txt):
            syms.append({"kind": "java.interface", "name": qname(m.group(1)), "path": fp})

        for m in enum_re.finditer(txt):
            syms.append({"kind": "java.enum", "name": qname(m.group(1)), "path": fp})

        for m in method_re.finditer(txt):
            ret = m.group(1).strip()
            name = m.group(2).strip()
            args = re.sub(r"\s+", " ", m.group(3).strip())
            syms.append({
                "kind": "java.method",
                "name": qname(name),
                "signature": f"{ret} {name}({args})",
                "path": fp,
            })

    return syms


def _extract_spring_endpoints() -> List[Dict[str, Any]]:
    files = _glob_many(["src/main/java/**/*.java"])
    eps: List[Dict[str, Any]] = []

    reqmap = re.compile(r'@RequestMapping\(\s*"(.*?)"\s*\)')
    getmap = re.compile(r'@GetMapping\(\s*"(.*?)"\s*\)')
    postmap = re.compile(r'@PostMapping\(\s*"(.*?)"\s*\)')
    putmap = re.compile(r'@PutMapping\(\s*"(.*?)"\s*\)')
    delmap = re.compile(r'@DeleteMapping\(\s*"(.*?)"\s*\)')

    for fp in files:
        if _is_test_path(fp):
            continue
        txt = _read_text(fp)
        if "@RestController" not in txt and "@Controller" not in txt:
            continue

        base = ""
        m = reqmap.search(txt)
        if m:
            base = m.group(1).strip()

        for ann, method in [(getmap, "GET"), (postmap, "POST"), (putmap, "PUT"), (delmap, "DELETE")]:
            for mm in ann.finditer(txt):
                route = mm.group(1).strip()
                full = (base.rstrip("/") + "/" + route.lstrip("/")).replace("//", "/") if base else route
                eps.append({"kind": "http.endpoint", "method": method, "route": full, "path": fp})

    return eps


def generate_contract_snapshot(run_req: Dict[str, Any]) -> Dict[str, Any]:
    """
    Snapshot heurístico de contrato: símbolos públicos + endpoints.
    Multi-stack: hoy implementamos Java+Spring (extensible).
    """
    lang = (run_req.get("language") or "").lower().strip()
    stack = str(run_req.get("stack") or "")

    symbols: List[Dict[str, Any]] = []
    endpoints: List[Dict[str, Any]] = []

    # Java/Spring
    if lang == "java" or stack.startswith("java-") or os.path.exists("pom.xml") or _glob_many(["src/main/java/**/*.java"]):
        symbols.extend(_extract_java_public_symbols())
        endpoints.extend(_extract_spring_endpoints())

    # Normalización: orden estable
    def _key(x: Dict[str, Any]) -> str:
        return json.dumps(x, ensure_ascii=False, sort_keys=True)

    symbols = sorted([s for s in symbols if isinstance(s, dict)], key=_key)
    endpoints = sorted([e for e in endpoints if isinstance(e, dict)], key=_key)

    payload = {
        "version": 1,
        "ts": int(time.time()),
        "stack": stack,
        "language": lang,
        "symbols": symbols,
        "endpoints": endpoints,
    }
    payload["hash"] = _snapshot_hash({
        "version": payload["version"],
        "stack": payload["stack"],
        "language": payload["language"],
        "symbols": payload["symbols"],
        "endpoints": payload["endpoints"],
    })
    return payload


def _index_symbols(symbols: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    key = (kind, name) -> record
    For methods: signature is used for lock
    """
    idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for s in symbols or []:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or "")
        name = str(s.get("name") or "")
        if not kind or not name:
            continue
        idx[(kind, name)] = s
    return idx


def _index_endpoints(eps: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    key = (method, route)
    """
    idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in eps or []:
        if not isinstance(e, dict):
            continue
        m = str(e.get("method") or "").upper()
        r = str(e.get("route") or "")
        if not m or not r:
            continue
        idx[(m, r)] = e
    return idx


def enforce_api_lock(lock: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforce "no breaking changes":
      - locked symbols/endpoints must still exist
      - method signatures must not change
    Allow additions.
    Returns a violation report (empty if OK).
    """
    violations: Dict[str, Any] = {
        "breaking": False,
        "removed_symbols": [],
        "changed_signatures": [],
        "removed_endpoints": [],
    }

    lock_syms = _index_symbols(lock.get("symbols") or [])
    cur_syms = _index_symbols(current.get("symbols") or [])

    # Symbols: must exist; if method signature present, must match
    for key, s in lock_syms.items():
        if key not in cur_syms:
            violations["removed_symbols"].append({"kind": key[0], "name": key[1], "path": s.get("path")})
            continue
        if key[0].endswith(".method"):
            old_sig = str(s.get("signature") or "")
            new_sig = str(cur_syms[key].get("signature") or "")
            if old_sig and new_sig and old_sig != new_sig:
                violations["changed_signatures"].append({
                    "name": key[1],
                    "from": old_sig,
                    "to": new_sig,
                    "path": cur_syms[key].get("path"),
                })

    lock_eps = _index_endpoints(lock.get("endpoints") or [])
    cur_eps = _index_endpoints(current.get("endpoints") or [])
    for key, e in lock_eps.items():
        if key not in cur_eps:
            violations["removed_endpoints"].append({"method": key[0], "route": key[1], "path": e.get("path")})

    if violations["removed_symbols"] or violations["changed_signatures"] or violations["removed_endpoints"]:
        violations["breaking"] = True

    return violations


def _has_shell_metachars(cmd: str) -> bool:
    return bool(re.search(r"[;&|`$><\n\r]", cmd or ""))


def is_safe_test_command(cmd: str, allowed_prefixes: List[str]) -> bool:
    """
    Enterprise safety:
    - Block shell metacharacters (; & | $ ` > < newline)
    - Allow if matches an allowlist of prefixes OR if the first token is an allowed runner binary.
      This avoids brittle exact-prefix matching across stacks.
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return True

    # hard block: never allow shell metacharacters
    if _has_shell_metachars(cmd):
        return False

    # normalize whitespace
    normalized = re.sub(r"\s+", " ", cmd).strip()

    # 1) Prefix allowlist (backward compatible)
    allowed = [re.sub(r"\s+", " ", a).strip() for a in (allowed_prefixes or []) if isinstance(a, str)]
    if any(normalized.lower().startswith(a.lower()) for a in allowed if a):
        return True

    # 2) Runner-binary allowlist (robust multi-stack)
    try:
        tokens = shlex.split(cmd)
    except Exception:
        return False

    if not tokens:
        return False

    first = tokens[0].lower()

    # Common safe runners across stacks
    allowed_bins = {
        "mvn", "./mvnw",
        "gradle", "./gradlew",
        "npm", "pnpm", "yarn",
        "dotnet",
        "go",
        "pytest", "python", "python3",
        "node",
    }

    if first not in allowed_bins:
        return False

    # extra rule: if first is python, only allow common test invocations (avoid arbitrary python scripts)
    if first in ("python", "python3"):
        # allow "python -m pytest ..." or "python -m unittest ..."
        if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in ("pytest", "unittest"):
            return True
        return False

    return True


def _is_transient_path(p: str) -> bool:
    pp = (p or "").replace("\\", "/").strip()
    if not pp:
        return True
    transient_prefixes = (
        "agent/out/",
        "agent/__pycache__/",
        "agent/stacks/__pycache__/",
        "agent/tools/__pycache__/",
        ".pytest_cache/",
        "__pycache__/",
        "target/",
        "build/",
        "dist/",
        ".mvn/",
        ".gradle/",
        "node_modules/",
        ".venv/",
        "venv/",
        ".tox/",
        ".coverage",
        "coverage/",
    )
    if pp.endswith(".pyc") or pp.endswith(".pyo"):
        return True
    return any(pp.startswith(x) or (x in ("__pycache__/",) and "/__pycache__/" in pp) for x in transient_prefixes)


def run_cmd(cmd: str) -> Tuple[int, str]:
    args = shlex.split(cmd)
    p = subprocess.run(args, text=True, capture_output=True)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return p.returncode, out


def detect_repo_changes() -> List[str]:
    changed: set[str] = set()

    code, out = run_cmd("git diff --name-only")
    if code == 0:
        for line in (out or "").splitlines():
            line = line.strip()
            if line:
                changed.add(line)

    code, st = run_cmd("git status --porcelain")
    if code == 0:
        for line in (st or "").splitlines():
            if not line.strip():
                continue
            path = line[3:].strip() if len(line) >= 4 else ""
            if path:
                if " -> " in path:
                    path = path.split(" -> ", 1)[1].strip()
                changed.add(path)

    filtered = [p for p in sorted(changed) if not _is_transient_path(p)]
    return filtered

    return sorted(changed)


def compact_memories(matches: List[Dict[str, Any]]) -> str:
    if not matches:
        return ""
    texts = []
    for m in matches[:8]:
        meta = m.get("metadata") or {}
        text = meta.get("text") or m.get("text") or ""
        if text:
            texts.append(str(text)[:500])
    return "\n---\n".join(texts)


def pine_upsert_event(repo: str, issue_number: str, kind: str, text: str, extra: Dict[str, Any] | None = None) -> None:
    if not pine_upsert:
        return
    try:
        payload = {
            "id": f"{kind}:{issue_number}:{os.environ.get('GITHUB_RUN_ID','')}-{os.environ.get('GITHUB_RUN_ATTEMPT','')}",
            "text": text,
            "metadata": {"kind": kind, **(extra or {})},
        }
        pine_upsert(repo, issue_number, [payload])
    except Exception:
        return


def write_out(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(content if content.endswith("\n") else content + "\n")


def render_summary_md(issue_title: str, pr_url: str, iteration_notes: List[str], test_report: Dict[str, Any]) -> str:
    md = []
    md.append("## 🤖 Agent 결과\n\n")
    md.append(f"**Issue:** {issue_title}\n\n")
    if pr_url:
        md.append(f"**PR:** {pr_url}\n\n")
    md.append("\n---\n")
    md.append("### Iteraciones\n")
    for n in iteration_notes:
        md.append(f"- {n}\n")
    md.append("\n---\n")
    md.append("### Test report\n")
    md.append(f"- Passed: `{test_report.get('passed')}`\n")
    md.append(f"- Summary: {test_report.get('summary','')}\n")
    if test_report.get("failure_hints"):
        md.append("\n### Failure hints\n")
        for h in test_report["failure_hints"][:12]:
            md.append(f"- {h}\n")
    return "".join(md)


def stable_failure_signature(test_exit: int, test_out: str) -> str:
    top = test_out.strip().splitlines()[:80]
    top = "\n".join(top)
    m2 = re.search(r"(ERROR:.*|AssertionError:.*|Traceback.*|FAIL:.*)", top, re.IGNORECASE)
    top = m2.group(1).strip() if m2 else ""
    return f"exit={test_exit}|{top[:200]}"


def _load_prompt(rel_path: str) -> str:
    return open(os.path.join(BASE_DIR, "prompts", rel_path), "r", encoding="utf-8").read()


def _attempt_plan_repair_once(
    *,
    invalid_plan: Dict[str, Any],
    validation_error: str,
    run_req: Dict[str, Any],
    issue_title: str,
    issue_body: str,
    repo_snap: Dict[str, str],
    memories: str,
) -> Dict[str, Any]:
    repair_prompt = _load_prompt("repair_plan_agent.md")

    write_out("agent/out/plan_invalid.json", json.dumps(invalid_plan, ensure_ascii=False, indent=2))
    write_out("agent/out/plan_validation_error.txt", validation_error)

    repaired = chat_json(
        system=repair_prompt,
        user=json.dumps({
            "schema_json": PLAN_SCHEMA,
            "invalid_plan_json": invalid_plan,
            "validation_error": validation_error,
            "context": {
                "stack": run_req.get("stack"),
                "language": run_req.get("language"),
                "user_story": run_req.get("user_story"),
                "acceptance_criteria": run_req.get("acceptance_criteria", []),
                "constraints": run_req.get("constraints", []),
                "issue_title": issue_title,
                "issue_body": issue_body,
                "repo_snapshot": repo_snap,
                "memories": memories,
            }
        }, ensure_ascii=False),
        schema_name="plan.schema.json",
    )

    repaired = normalize_plan(repaired)
    write_out("agent/out/plan_repaired.json", json.dumps(repaired, ensure_ascii=False, indent=2))
    return repaired

def parse_pytest_telemetry(out: str) -> Dict[str, int]:
    txt = (out or "")
    def _m(pat: str) -> int:
        m = re.search(pat, txt, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    passed = _m(r"(\d+)\s+passed")
    failed = _m(r"(\d+)\s+failed")
    errors = _m(r"(\d+)\s+error")
    skipped = _m(r"(\d+)\s+skipped")
    xfailed = _m(r"(\d+)\s+xfailed")
    xpassed = _m(r"(\d+)\s+xpassed")
    total = passed + failed + errors + skipped + xfailed + xpassed
    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "xfailed": xfailed,
        "xpassed": xpassed,
        "total": total,
    }


def discover_maven_surefire_tests() -> Dict[str, Any]:
    """
    Lee target/surefire-reports/TEST-*.xml y devuelve:
      - total tests, failures, errors, skipped
      - lista de clases detectadas
      - lista corta de testcases (class#name)
    """
    import glob
    import xml.etree.ElementTree as ET

    reports_dir = "target/surefire-reports"
    xml_files = sorted(glob.glob(os.path.join(reports_dir, "TEST-*.xml")))
    if not xml_files:
        return {
            "reports_dir": reports_dir,
            "xml_files": [],
            "total": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "classes": [],
            "testcases": [],
            "note": "No surefire XML reports found (maybe tests didn't run, or different runner).",
        }

    total = failures = errors = skipped = 0
    classes = set()
    testcases = []

    for xf in xml_files:
        try:
            root = ET.parse(xf).getroot()
            # root is <testsuite>
            cls = root.attrib.get("name", "")
            if cls:
                classes.add(cls)

            t = int(root.attrib.get("tests", "0") or 0)
            f = int(root.attrib.get("failures", "0") or 0)
            e = int(root.attrib.get("errors", "0") or 0)
            s = int(root.attrib.get("skipped", "0") or 0)

            total += t
            failures += f
            errors += e
            skipped += s

            # collect some testcases (bounded)
            for tc in root.findall(".//testcase"):
                cname = tc.attrib.get("classname", cls) or cls
                name = tc.attrib.get("name", "")
                if cname or name:
                    testcases.append(f"{cname}#{name}")
                if len(testcases) >= 200:
                    break
        except Exception:
            continue

    return {
        "reports_dir": reports_dir,
        "xml_files": xml_files[:50],
        "total": total,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "classes": sorted(classes),
        "testcases": testcases[:200],
    }


def main() -> None:
    repo = os.environ["REPO"]
    issue_number = str(os.environ["ISSUE_NUMBER"])
    comment_body = os.environ.get("COMMENT_BODY", "")

    # Always produce traceability files (even on failures)
    os.makedirs("agent/out", exist_ok=True)
    write_out("agent/out/branch.txt", "")
    write_out("agent/out/pr_url.txt", "")

    issue = get_issue(repo, issue_number)
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "") or ""

    run_req = extract_json_from_comment(comment_body)

    catalog = load_catalog()

    # --- ENTERPRISE: stack mismatch guard + auto fallback by markers ---
    requested_stack = str(run_req.get("stack") or "").strip()
    explicit_stack = bool(requested_stack and requested_stack.lower() not in ("auto",))

    spec = resolve_stack_spec(run_req, repo_root=".", catalog=catalog)

    if explicit_stack and not _markers_found_for_stack(catalog, spec.stack_id):
        # If repo looks like SOME other stack, do NOT try to run tests with a mismatched stack.
        if _repo_has_any_stack_markers(catalog):
            fallback_req = dict(run_req)
            fallback_req["stack"] = "auto"
            spec2 = resolve_stack_spec(fallback_req, repo_root=".", catalog=catalog)

            if _markers_found_for_stack(catalog, spec2.stack_id):
                write_out(
                    "agent/out/stack_resolution.txt",
                    (
                        f"Stack mismatch: requested='{requested_stack}' but markers not found. "
                        f"Fallback to auto-detected='{spec2.stack_id}'."
                    ),
                )
                spec = spec2
                run_req["stack"] = spec.stack_id
                run_req["language"] = spec.language
            else:
                raise ValueError(
                    f"Stack mismatch: requested='{requested_stack}' but no markers found, and auto-detect also failed. "
                    "Corrige 'stack' o agrega bootstrap en el StackSpec."
                )
        else:
            # Repo sin markers detectables (greenfield o repo-orquestador).
            # NO fallar si el stack solicitado define bootstrap: stack_setup hará scaffold.
            bk = _bootstrap_kind_for_stack(catalog, requested_stack)

            write_out(
                "agent/out/stack_resolution.txt",
                (
                    f"Repo has no stack markers. Keeping requested stack='{requested_stack}'. "
                    f"bootstrap.kind='{bk}'. stack_setup will scaffold if configured."
                ),
            )

            # Fail-fast SOLO si no hay bootstrap definido para este stack
            if bk in ("none", "", "unknown"):
                raise ValueError(
                    f"Stack mismatch: requested='{requested_stack}' pero el repo no contiene markers "
                    "y este stack no define bootstrap. Usa stack='auto' o agrega bootstrap en catalog.yml."
                )

    run_req["stack"] = spec.stack_id or (run_req.get("stack") or "auto")
    run_req["language"] = spec.language or (run_req.get("language") or "generic")
    if spec.commands.test:
        run_req["test_command"] = spec.commands.test

    safe_validate(run_req, RUN_SCHEMA, "run_request.schema.json")

    language = (run_req.get("language") or "").lower().strip()

    allowed_prefixes = spec.allowed_test_prefixes or []
    if run_req.get("test_command") and not is_safe_test_command(str(run_req.get("test_command")), allowed_prefixes):
        raise ValueError(
            "test_command rechazado por seguridad. "
            "Usa un comando estándar del stack (sin ; & | $ ` > < ni saltos de línea)."
        )

    memories = ""
    if pine_query:
        try:
            mem_query_text = f"{run_req.get('stack')} | {run_req.get('language')} | {run_req.get('user_story')} | {issue_title}"
            mem_matches = pine_query(repo, issue_number, mem_query_text, top_k=8)
            memories = compact_memories(mem_matches)
        except Exception as e:
            memories = f"(memory disabled: {e})"

    files = list_files(".")
    key_candidates = [p for p in files if any(p.endswith(suf) for suf in [
        "pyproject.toml", "requirements.txt", "package.json", "package-lock.json",
        "pom.xml", "build.gradle", "gradlew", "go.mod",
        ".sln", ".csproj",
        "README.md", "Makefile", "pytest.ini", "tox.ini"
    ])]
    key_candidates = list(dict.fromkeys(key_candidates))[:60]
    repo_snap = snapshot(key_candidates)

    planner_prompt = _load_prompt("design_agent.md")
    impl_prompt = _load_prompt("implement_agent.md")
    test_prompt = _load_prompt("test_agent.md")

    # Planner -> Plan (repair max 1)
    plan_raw = chat_json(
        system=planner_prompt,
        user=json.dumps({
            "stack": run_req.get("stack"),
            "language": run_req.get("language"),
            "user_story": run_req.get("user_story"),
            "acceptance_criteria": run_req.get("acceptance_criteria", []),
            "constraints": run_req.get("constraints", []),
            "repo_snapshot": repo_snap,
            "issue_title": issue_title,
            "issue_body": issue_body,
            "memories": memories
        }, ensure_ascii=False),
        schema_name="plan.schema.json",
    )

    plan = normalize_plan(plan_raw)

    repaired_once = False
    try:
        safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")
    except ValueError as e:
        if repaired_once:
            raise
        repaired_once = True

        plan = _attempt_plan_repair_once(
            invalid_plan=plan_raw,
            validation_error=str(e),
            run_req=run_req,
            issue_title=issue_title,
            issue_body=issue_body,
            repo_snap=repo_snap,
            memories=memories,
        )

        try:
            safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")
        except ValueError as e2:
            write_out("agent/out/plan_repair_failed.txt", str(e2))
            raise ValueError(
                "Plan inválido incluso después de 1 intento de repair. "
                "Revisa agent/out/plan_invalid.json, plan_validation_error.txt, plan_repaired.json, plan_repair_failed.txt"
            ) from e2

    pine_upsert_event(repo, issue_number, "plan", json.dumps(plan, ensure_ascii=False), {
        "stack": run_req.get("stack"),
        "language": run_req.get("language"),
        "issue_title": issue_title,
    })

    max_iterations = int(run_req.get("max_iterations", 2))
    base_branch = "main"
    branch_name = f"agent/issue-{issue_number}"
    # Create or reuse the work branch (idempotent across reruns)
    ensure_branch(branch_name, base=f"origin/{base_branch}")
    write_out("agent/out/branch.txt", branch_name)

    pr_url = ""
    iteration_notes: List[str] = []
    test_report: Dict[str, Any] = {"passed": False, "summary": "not run"}

    last_failure_sig = ""
    stuck_count = 0

    for i in range(1, max_iterations + 1):
        prev_test_output = ""
        if os.path.exists("agent/out/last_test_output.txt"):
            prev_test_output = open(
                "agent/out/last_test_output.txt",
                "r",
                encoding="utf-8",
                errors="replace"
            ).read()

        prev_hints: List[str] = []
        if os.path.exists("agent/out/failure_hints.json"):
            try:
                prev_hints = json.loads(
                    open("agent/out/failure_hints.json", "r", encoding="utf-8").read()
                )
            except Exception:
                prev_hints = []

        # IMPLEMENT
        patch_obj = chat_json(
            system=impl_prompt,
            user=json.dumps({
                "iteration": i,
                "stack": run_req.get("stack"),
                "language": run_req.get("language"),
                "user_story": run_req.get("user_story"),
                "acceptance_criteria": run_req.get("acceptance_criteria", []),
                "constraints": run_req.get("constraints", []),
                "plan": plan,
                "repo_snapshot": repo_snap,
                "memories": memories,
                "previous_test_output": prev_test_output,
                "failure_hints": prev_hints,
            }, ensure_ascii=False),
            schema_name="patch.schema.json",
        )

        # DEBUG: patch crudo
        write_out(
            f"agent/out/iter_{i}_patch_from_llm.json",
            json.dumps(patch_obj, ensure_ascii=False, indent=2),
        )

        patch_obj = normalize_patch(patch_obj)

        # Hard guard: no permitir patches vacíos
        if "patches" in patch_obj and (not isinstance(patch_obj["patches"], list) or len(patch_obj["patches"]) == 0):
            patch_obj.pop("patches", None)

        safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")

        apply_patch_object(patch_obj)

        # patch normalizado aplicado
        write_out(f"agent/out/iter_{i}_patch.json", json.dumps(patch_obj, ensure_ascii=False, indent=2))

        # Detect changes
        changed_files = detect_repo_changes()
        changed_text = "\n".join(changed_files)
        write_out(f"agent/out/iter_{i}_changed_files.txt", changed_text)

        if not changed_files:
            iteration_notes.append(f"Iteración {i}: sin cambios detectados (skip)")
            continue

        # -------------------------------
        # ENTERPRISE POLICY: Financial Test Contract (multi-stack)
        # Prohibir expected hardcodeado "manual/derivado" en tests financieros.
        # Si se viola, revertimos cambios y forzamos al implementador a derivar expected por fórmula/helper.
        # -------------------------------
        policy = detect_financial_expected_antipattern(changed_files)
        write_out(
            f"agent/out/iter_{i}_policy_violation_financial_tests.json",
            json.dumps(policy, ensure_ascii=False, indent=2),
        )

        if policy.get("breaking"):
            # Revertir cambios locales para no ensuciar la rama con commits malos
            try:
                subprocess.run(["git", "checkout", "--", "."], text=True, capture_output=True)
                subprocess.run(["git", "clean", "-fd"], text=True, capture_output=True)
            except Exception:
                pass

            msg = (
                "POLICY VIOLATION: Tests financieros con expected hardcodeado marcado como 'manual/derivado'.\n"
                "Regla enterprise: expected debe derivarse por fórmula estándar (helper en test) o usar un golden vector documentado.\n"
                f"Ver evidencia: agent/out/iter_{i}_policy_violation_financial_tests.json\n"
                "Sugerencia: en Python/pytest define una función helper expected_payment(...) con la fórmula y compara con pytest.approx.\n"
            )
            write_out("agent/out/last_test_output.txt", msg)
            write_out("agent/out/failure_hints.json", json.dumps([
                "POLICY: No hardcodear expected 'manual/derivado' en tests financieros. Deriva expected por fórmula/helper o golden vector documentado.",
                "Python/pytest: define expected_payment(principal, annual_rate, years_or_months) usando la fórmula de amortización y usa pytest.approx.",
                "Si cambias unidad (years↔months), NO rompas contrato: crea v2 o wrapper compatible (API LOCK).",
            ], ensure_ascii=False, indent=2))

            iteration_notes.append(f"Iteración {i}: ❌ policy violation (financial expected hardcoded) -> reverted")
            continue

        # --- CONTRACT SNAPSHOT + API LOCK (enterprise, stack-agnostic by heuristics) ---
        snap = generate_contract_snapshot(run_req)
        write_out(f"agent/out/iter_{i}_contract_snapshot.json", json.dumps(snap, ensure_ascii=False, indent=2))
        write_out("agent/out/contract_snapshot.json", json.dumps(snap, ensure_ascii=False, indent=2))

        lock_path = "agent/out/contract_lock.json"
        if not os.path.exists(lock_path):
            # Initialize lock on first iteration that actually changes files.
            # This prevents "years↔months" drift in later iterations.
            write_out(lock_path, json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            try:
                lock = json.loads(open(lock_path, "r", encoding="utf-8").read())
            except Exception:
                lock = None

            if isinstance(lock, dict):
                violations = enforce_api_lock(lock, snap)
                write_out(f"agent/out/iter_{i}_contract_violations.json", json.dumps(violations, ensure_ascii=False, indent=2))

                if violations.get("breaking"):
                    # Revert uncommitted changes to keep repo clean
                    try:
                        subprocess.run(["git", "checkout", "--", "."], text=True, capture_output=True)
                        subprocess.run(["git", "clean", "-fd"], text=True, capture_output=True)
                    except Exception:
                        pass

                    raise RuntimeError(
                        f"Contract/API lock violado: se detectaron breaking changes (eliminación o cambio de firma/endpoint). "
                        f"Revisa agent/out/iter_{i}_contract_violations.json. "
                        "Política: solo cambios aditivos; si necesitas cambiar contrato, crea wrapper compatible o v2."
                    )

        # git status debug
        _, status_porcelain = run_cmd("git status --porcelain")
        write_out(f"agent/out/iter_{i}_git_status.txt", status_porcelain)

        git_commit_all(f"agent: implement issue {issue_number} (iter {i})")

        # RUN TESTS (sin shell)
        test_cmd = run_req.get("test_command") or ""
        test_exit, test_out = run_cmd(test_cmd)

        telemetry = {}
        effective_exit = int(test_exit)

        # --- ENTERPRISE: meaningful-tests gate (esp. pytest exit=0 con skipped/0 tests) ---
        if (run_req.get("language") or "").lower().strip() == "python":
            telemetry = parse_pytest_telemetry(test_out)
            write_out(f"agent/out/iter_{i}_test_telemetry.json", json.dumps(telemetry, ensure_ascii=False, indent=2))

            # Si pytest exit=0 pero no ejecutó tests "reales", forzar fallo lógico
            no_meaningful = (telemetry.get("total", 0) == 0) or (
                telemetry.get("passed", 0) == 0 and telemetry.get("failed", 0) == 0 and telemetry.get("errors", 0) == 0
            )
            if effective_exit == 0 and no_meaningful:
                effective_exit = 2  # consistente con "test failures"
                test_out = (test_out or "") + "\n\n[enterprise] No meaningful tests executed (all skipped or none collected). Treating as failure.\n"

        # usar effective_exit desde aquí en adelante
        test_exit = effective_exit

        # Persist both per-iteration and "last" for convenience
        write_out(f"agent/out/iter_{i}_test_output.txt", test_out)
        write_out("agent/out/last_test_output.txt", test_out)

        # --- ENTERPRISE: Java test discovery (Surefire) ---
        if str(run_req.get("stack") or "").startswith("java-") or (run_req.get("language") or "").lower() == "java":
            surefire = discover_maven_surefire_tests()
            write_out(f"agent/out/iter_{i}_surefire.json", json.dumps(surefire, ensure_ascii=False, indent=2))

            # Optional: detect if the agent-created tests actually ran
            expected_tests = []
            files_map = patch_obj.get("files") if isinstance(patch_obj.get("files"), dict) else {}
            for pth in files_map.keys():
                p_norm = pth.strip()
                if p_norm.startswith("./"):
                    p_norm = p_norm[2:]
                p_norm = os.path.normpath(p_norm)
                if p_norm.startswith(os.path.join("src", "test", "java")) and p_norm.endswith(".java"):
                    # map path -> FQN guess: src/test/java/a/b/C.java => a.b.C
                    rel = p_norm[len(os.path.join("src", "test", "java")) + 1 :]
                    fqn = rel[:-5].replace(os.sep, ".")
                    expected_tests.append(fqn)

            if expected_tests:
                ran_classes = set(surefire.get("classes") or [])
                missing = [t for t in expected_tests if t not in ran_classes]
                write_out(
                    f"agent/out/iter_{i}_expected_tests.json",
                    json.dumps({"expected": expected_tests, "missing_in_surefire": missing}, ensure_ascii=False, indent=2),
                )

        # FAILURE HINTS
        try:
            meta = fh_classify_failure(
                test_out,
                language=language,
                stack=str(run_req.get("stack") or ""),
            )
            hints = summarize_hints(meta)
            write_out("agent/out/failure_hints.json", json.dumps(hints, ensure_ascii=False, indent=2))
        except Exception:
            hints = []

        # Pinecone upsert after tests
        pine_upsert_event(
            repo,
            issue_number,
            "iteration",
            f"iter={i}\nchanged_files=\n{changed_text}\n\nexit={test_exit}\n\n{test_out[:4000]}",
            {
                "iteration": i,
                "exit": int(test_exit),
                "changed_files_count": len(changed_files),
                "stack": run_req.get("stack"),
                "language": run_req.get("language"),
            }
        )

        # TEST AGENT
        tr = chat_json(
            system=test_prompt,
            user=json.dumps({
                "stack": run_req.get("stack"),
                "language": run_req.get("language"),
                "user_story": run_req.get("user_story"),
                "acceptance_criteria": run_req.get("acceptance_criteria", []),
                "constraints": run_req.get("constraints", []),
                "plan": plan,
                "test_command": test_cmd,
                "test_exit": test_exit,
                "test_output": test_out[:12000],
                "failure_hints": hints,
            }, ensure_ascii=False),
            schema_name="test_report.schema.json",
        )

        test_report = normalize_test_report(tr, run_req)
        safe_validate(test_report, TEST_SCHEMA, "test_report.schema.json")

        # -------------------------------
        # ENTERPRISE: Early stop on success
        # Avoid regressions by continuing to iterate after tests already pass.
        # This is stack-agnostic.
        # -------------------------------
        try:
            passed = bool(test_report.get("passed", False))
        except Exception:
            passed = False

        ac_status = test_report.get("acceptance_criteria_status", [])
        all_met = False
        if isinstance(ac_status, list) and ac_status:
            all_met = all(bool(x.get("met", False)) for x in ac_status if isinstance(x, dict))
        else:
            # If no AC provided, treat "passed" as sufficient
            all_met = True

        if int(test_exit) == 0 and passed and all_met:
            iteration_notes.append(f"Iteración {i}: ✅ passed (early stop)")
            break

        iteration_notes.append(f"Iteración {i}: test exit={test_exit} passed={bool(test_report.get('passed', False))}")

        failure_sig = stable_failure_signature(test_exit, test_out)

        try:
            stuck = should_count_as_stuck(last_failure_sig, failure_sig, changed_text)
        except TypeError:
            stuck = should_count_as_stuck(last_failure_sig, failure_sig)

        if stuck:
            stuck_count += 1
        else:
            stuck_count = 0

        last_failure_sig = failure_sig

    # -------------------------------
    # ENTERPRISE: Create PR when there are commits
    # - Always write branch.txt and pr_url.txt for traceability
    # - Idempotent: reuses existing PR if present
    # -------------------------------
    commits_ahead = git_commits_ahead(f"origin/{base_branch}", "HEAD")
    if commits_ahead > 0:
        try:
            git_push(branch_name)
        except Exception as e:
            # Surface push failures clearly (often token/permissions)
            write_out("agent/out/push_failed.txt", str(e))
            raise

        pr_title = f"agent: issue #{issue_number}"
        pr_body = (
            f"Automated changes for issue #{issue_number}.\n\n"
            f"- Stack: {run_req.get('stack')}\n"
            f"- Language: {run_req.get('language')}\n"
            f"- Iterations: {len(iteration_notes)}\n\n"
            "See agent/out artifacts for details."
        )
        pr_url = gh_pr_ensure(title=pr_title, body=pr_body, head=branch_name, base=base_branch)

    write_out("agent/out/pr_url.txt", pr_url or "")

    summary_md = render_summary_md(issue_title, pr_url, iteration_notes, test_report)
    write_out("agent/out/summary.md", summary_md)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        msg = f"❌ Orchestrator error: {e}"
        os.makedirs("agent/out", exist_ok=True)
        with open("agent/out/summary.md", "w", encoding="utf-8", errors="replace") as f:
            f.write(msg + "\n")
        raise
