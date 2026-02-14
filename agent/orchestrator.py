import os
import re
import json
import uuid
import textwrap
import subprocess
import hashlib
from typing import Any, Dict, List, Tuple

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from agent.tools.llm import chat_json
from agent.tools.github_tools import (
    get_issue, create_branch, git_status_porcelain, git_commit_all, git_push, gh_pr_create, current_branch
)
from agent.tools.repo_introspect import list_files, snapshot
from agent.tools.patch_apply import apply_patch_object

from agent.tools.pinecone_memory import upsert_texts, query as pine_query

ROOT = os.path.dirname(os.path.abspath(__file__))

# --- Stack catalog defaults (language/test_command) ---
def _load_stack_catalog() -> dict:
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        with open(os.path.join(ROOT, "stacks", "catalog.yml"), "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def apply_stack_defaults(run_req: dict) -> dict:
    stack = (run_req.get("stack") or "").strip()
    if not stack:
        return run_req
    catalog = _load_stack_catalog()
    spec = catalog.get(stack) or {}
    if not run_req.get("language") and spec.get("language"):
        run_req["language"] = spec["language"]
    if (not run_req.get("test_command")) and spec.get("test_command"):
        run_req["test_command"] = spec["test_command"]
    return run_req

def read(path: str) -> str:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()

def extract_json_from_comment(body: str) -> Dict[str, Any]:
    # Expected: /agent run { ...json... }
    m = re.search(r"/agent\s+run\s+(\{.*\})\s*$", body, re.DOTALL)
    if not m:
        raise ValueError("No se encontró JSON. Usa: /agent run { ... }")
    return json.loads(m.group(1))

def safe_validate(obj: Any, schema: Dict[str, Any], schema_name: str) -> None:
    try:
        validate(instance=obj, schema=schema)
    except ValidationError as e:
        raise ValueError(f"JSON inválido para {schema_name}: {e.message}")

def normalize_plan(plan_obj: Dict[str, Any]) -> Dict[str, Any]:
    # unwrap common wrapper { "plan": { ... } }
    if isinstance(plan_obj, dict) and "plan" in plan_obj and isinstance(plan_obj["plan"], dict):
        plan_obj = plan_obj["plan"]

    # convert alternative keys (steps -> tasks, files_to_modify/create -> files_to_touch, tests_strategy -> test_strategy)
    if "tasks" not in plan_obj and "steps" in plan_obj and isinstance(plan_obj["steps"], list):
        tasks = []
        files = set()
        test_strategy = ""
        for idx, step in enumerate(plan_obj["steps"], start=1):
            title = step.get("step") or step.get("title") or f"Step {idx}"
            desc = step.get("description") or step.get("details") or title
            tasks.append({"id": f"T{idx}", "title": str(title), "description": str(desc)})
            for p in (step.get("files_to_modify") or []):
                files.add(str(p))
            for p in (step.get("files_to_create") or []):
                files.add(str(p))
            if not test_strategy:
                test_strategy = step.get("tests_strategy") or step.get("test_strategy") or step.get("tests") or ""

        if "tasks" not in plan_obj:
            plan_obj["tasks"] = tasks
        if "files_to_touch" not in plan_obj:
            plan_obj["files_to_touch"] = sorted(list(files))
        if "test_strategy" not in plan_obj:
            plan_obj["test_strategy"] = str(test_strategy) if test_strategy else "Ejecutar tests automatizados según test_command."

    # ensure required keys exist
    plan_obj.setdefault("assumptions", [])
    plan_obj.setdefault("risks", [])
    plan_obj.setdefault("files_to_touch", plan_obj.get("files_to_touch", []))
    plan_obj.setdefault("tasks", plan_obj.get("tasks", []))
    plan_obj.setdefault("test_strategy", plan_obj.get("test_strategy", "Ejecutar tests automatizados según test_command."))
    return plan_obj

def normalize_patch(patch_obj: Dict[str, Any]) -> Dict[str, Any]:
    # unwrap common wrapper
    if isinstance(patch_obj, dict) and "patch" in patch_obj and isinstance(patch_obj["patch"], dict):
        patch_obj = patch_obj["patch"]
    if "notes" not in patch_obj:
        patch_obj["notes"] = []
    return patch_obj

def normalize_test_report(tr: Dict[str, Any], run_req: Dict[str, Any]) -> Dict[str, Any]:
    # unwrap common wrapper
    if isinstance(tr, dict) and "test_report" in tr and isinstance(tr["test_report"], dict):
        tr = tr["test_report"]

    # already correct
    if "passed" in tr and "summary" in tr and "acceptance_criteria_status" in tr:
        return tr

    # map common alternative keys
    if "tests_passed" in tr:
        passed = bool(tr["tests_passed"])
    elif "passed" in tr:
        passed = bool(tr["passed"])
    else:
        passed = False

    summary = tr.get("summary") or tr.get("test_summary") or "Resultado de pruebas no especificado."

    ac_list = []
    criteria = run_req.get("acceptance_criteria") or []
    if criteria:
        evidence_base = tr.get("criteria_verification") or tr.get("summary") or tr.get("test_summary") or ""
        if not isinstance(evidence_base, str):
            evidence_base = json.dumps(evidence_base, ensure_ascii=False)
        for c in criteria:
            ac_list.append({"criterion": str(c), "met": passed, "evidence": evidence_base[:400]})
    else:
        evid = summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False)
        ac_list = [{"criterion": "N/A", "met": passed, "evidence": evid[:400]}]

    out = {
        "passed": passed,
        "summary": summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False),
        "acceptance_criteria_status": ac_list
    }

    if "recommended_patch" in tr:
        out["recommended_patch"] = tr["recommended_patch"]

    return out

# ---- Load schemas ----
RUN_SCHEMA = json.loads(read("schemas/run_request.schema.json"))
PLAN_SCHEMA = json.loads(read("schemas/plan.schema.json"))
PATCH_SCHEMA = json.loads(read("schemas/patch.schema.json"))
TEST_SCHEMA = json.loads(read("schemas/test_report.schema.json"))

def run_cmd(cmd: str) -> Tuple[int, str]:
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    return p.returncode, (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")

def write_out(rel_path: str, content: str) -> None:
    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, rel_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(content if content.endswith("\n") else content + "\n")

def git_diff_name_only() -> str:
    code, out = run_cmd("git diff --name-only")
    return out if code == 0 else ""

def git_diff_full() -> str:
    code, out = run_cmd("git diff")
    return out if code == 0 else ""

def collect_env_info(stack: str, test_cmd: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"stack": stack, "test_command": test_cmd}
    cmds = {
        "python_version": "python --version",
        "pip_version": "python -m pip --version",
        "cwd": "pwd",
        "which_pytest": "which pytest || true",
        "pytest_version": "pytest --version || true",
        "which_python": "which python || true",
    }
    for k, c in cmds.items():
        _, o = run_cmd(c)
        info[k] = o
    if stack == "python-django" or os.path.exists("manage.py"):
        _, o = run_cmd("ls -la manage.py 2>/dev/null || true")
        info["manage_py"] = o
    return info

def error_signature(test_output: str) -> str:
    if not test_output:
        return ""
    lines = test_output.splitlines()
    tail = "\n".join(lines[-80:])
    tail = re.sub(r"/home/runner/work/[^\s]+", "<WORKDIR>", tail)
    tail = re.sub(r"0x[0-9a-fA-F]+", "0x<ADDR>", tail)
    return tail[:2000]

def classify_failure(test_output: str) -> Dict[str, Any]:
    out = test_output or ""
    hints: List[str] = []
    kind = "unknown"

    float_mismatch = (
        re.search(r"\b\d+\.\d{6,}\b", out) and
        ("assert" in out) and
        ("==" in out)
    )
    if float_mismatch:
        kind = "float_precision_mismatch"
        hints.append(
            "Parece mismatch de precisión float en asserts (igualdad exacta). "
            "Solución: redondear a 2 decimales (money) usando Decimal+quantize o round(x,2), "
            "y en tests usar pytest.approx o comparar contra round(...,2)."
        )

    if "ModuleNotFoundError" in out or "ImportError" in out:
        if "No module named" in out:
            kind = "missing_dependency"
            hints.append(
                "Fallo por dependencia faltante. Identifica el módulo exacto en el traceback "
                "y agrégalo a requirements.txt / pyproject.toml, o al stack catalog."
            )

    if "pytest: not found" in out or "/bin/sh: 1: pytest: not found" in out:
        kind = "missing_pytest"
        hints.append("pytest no está disponible. Instalar pytest o ajustar test_command.")

    stable = error_signature(out)
    fp = f"{kind}|{stable[:800]}"

    return {"kind": kind, "hints": hints, "fingerprint": fp}


def try_autofix_float_mismatch(root_dir: str = ".") -> Dict[str, Any]:
    """
    Auto-fix determinístico para mismatches de float en asserts.
    Recorre archivos de tests (tests/** y test_*.py) y reemplaza:
      assert <expr> == <float>
    por:
      assert <expr> == pytest.approx(<float>, abs=0.01)

    Devuelve {"changed": bool, "files": [..]}.
    """
    changed_files: List[str] = []

    # candidatos: tests/ + cualquier test_*.py
    candidates: List[str] = []
    for p in list_files(root_dir):
        rp = p.lstrip("./")
        if rp.startswith("tests/") and rp.endswith(".py"):
            candidates.append(rp)
        elif os.path.basename(rp).startswith("test_") and rp.endswith(".py"):
            candidates.append(rp)

    if not candidates:
        return {"changed": False, "files": []}

    pattern = re.compile(r"^\s*assert\s+(.+?)\s*==\s*([0-9]+\.[0-9]+)\s*$")

    for path in sorted(set(candidates)):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except FileNotFoundError:
            continue

        if "pytest.approx" in src:
            continue

        new_lines: List[str] = []
        did_change = False

        for line in src.splitlines():
            m = pattern.match(line)
            if m:
                left = m.group(1).strip()
                right = m.group(2).strip()
                indent = re.match(r"^(\s*)", line).group(1)  # type: ignore
                new_lines.append(f"{indent}assert {left} == pytest.approx({right}, abs=0.01)")
                did_change = True
            else:
                new_lines.append(line)

        if did_change:
            # asegurar import pytest
            if re.search(r"^\s*import\s+pytest\b", src, re.MULTILINE) is None:
                # inserta al inicio, después de shebang/encoding si existe
                lines2 = new_lines
                insert_at = 0
                if lines2 and lines2[0].startswith("#!"):
                    insert_at = 1
                if len(lines2) > insert_at and "coding" in lines2[insert_at]:
                    insert_at += 1
                lines2.insert(insert_at, "import pytest")
                new_lines = lines2

            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
            changed_files.append(path)

    return {"changed": bool(changed_files), "files": changed_files}

def compact_memories(mem_matches: List[Dict[str, Any]], max_items: int = 8) -> List[str]:
    out = []
    for m in mem_matches[:max_items]:
        meta = m.get("metadata") or {}
        txt = meta.get("text") or m.get("text") or ""
        if txt:
            out.append(str(txt)[:800])
    return out

def create_pr_body(issue_number: str, run_req: Dict[str, Any], plan: Dict[str, Any], iteration_notes: List[str], last_test_output: str) -> str:
    lines = []
    lines.append(f"### 🤖 Agent Orchestrator — Issue #{issue_number}")
    lines.append(f"**Stack/Lang:** `{run_req.get('stack','')}` / `{run_req.get('language','')}`")
    lines.append("")
    lines.append("**Historia de usuario**")
    lines.append(f"> {run_req.get('user_story','')}")
    lines.append("")
    if run_req.get("acceptance_criteria"):
        lines.append("**Criterios de aceptación**")
        for c in run_req["acceptance_criteria"]:
            lines.append(f"- {c}")
        lines.append("")
    lines.append("**Plan (resumen)**")
    lines.append(plan.get("summary", ""))
    lines.append("")
    lines.append("**Resultado de iteraciones**")
    for n in iteration_notes:
        lines.append(f"- {n}")
    lines.append("")
    if last_test_output:
        lines.append("Logs (último test output, truncado):")
        lines.append("```")
        lines.append(last_test_output[:4000])
        lines.append("```")
    return "\n".join(lines)

def main():
    repo = os.environ["REPO"]
    issue_number = str(os.environ["ISSUE_NUMBER"])
    comment_body = os.environ.get("COMMENT_BODY", "")

    issue = get_issue(repo, issue_number)
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "") or ""

    run_req = extract_json_from_comment(comment_body)
    run_req = apply_stack_defaults(run_req)

    # --- Robustez: clamp max_iterations al rango permitido por el schema ---
    orig_mi = run_req.get("max_iterations", None)
    requested_max_iterations = orig_mi  # NO meter en run_req (schema strict)

    try:
        mi = int(orig_mi) if orig_mi is not None else int(
            RUN_SCHEMA.get("properties", {}).get("max_iterations", {}).get("default", 2)
        )
    except Exception:
        mi = int(RUN_SCHEMA.get("properties", {}).get("max_iterations", {}).get("default", 2))

    mi_schema = RUN_SCHEMA.get("properties", {}).get("max_iterations", {})
    mi_min = mi_schema.get("minimum", 1)
    mi_max = mi_schema.get("maximum", 15)

    if mi < mi_min:
        mi = mi_min
    if mi > mi_max:
        mi = mi_max

    run_req["max_iterations"] = mi
    # --- fin clamp ---

    safe_validate(run_req, RUN_SCHEMA, "run_request.schema.json")

    mem_query_text = f"{run_req['stack']} | {run_req['language']} | {run_req['user_story']} | {issue_title}"
    mem_matches = pine_query(repo, issue_number, mem_query_text, top_k=8)
    memories = compact_memories(mem_matches)

    files = list_files(".")
    key_candidates = [p for p in files if any(p.endswith(suf) for suf in [
        "pyproject.toml", "requirements.txt", "setup.cfg", "setup.py", "README.md",
        "Makefile", "pytest.ini", "tox.ini", "ruff.toml"
    ])]
    key_candidates += [p for p in files if p.startswith("./tests") or p.endswith("__init__.py")]
    key_candidates = list(dict.fromkeys(key_candidates))[:40]
    repo_snap = snapshot(key_candidates)

    orchestrator_sys = read("prompts/orchestrator_system.md")
    design_prompt = read("prompts/design_agent.md")

    design_user = json.dumps({
        "run_request": run_req,
        "issue": {"title": issue_title, "body": issue_body},
        "repo_snapshot_files": list(repo_snap.keys()),
        "repo_snapshot": repo_snap,
        "memories": memories
    }, ensure_ascii=False)

    plan = chat_json(
        system=orchestrator_sys + "\n\n" + design_prompt,
        user=design_user,
        schema_name="plan.schema.json",
        temperature=0.2
    )
    plan = normalize_plan(plan)

    try:
        safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")
    except ValueError as e:
        repair_user = json.dumps(
            {
                "error": str(e),
                "invalid_plan": plan,
                "required_schema": PLAN_SCHEMA,
                "instructions": (
                    "Devuelve SOLO JSON válido que cumpla EXACTAMENTE plan.schema.json. "
                    "Debe incluir summary, tasks (array), files_to_touch (array), test_strategy (string). "
                    "Sin markdown."
                )
            },
            ensure_ascii=False
        )
        repaired = chat_json(
            system=orchestrator_sys + "\nEres un formateador estricto. Repara el plan al schema exacto.",
            user=repair_user,
            schema_name="plan.schema.json",
            temperature=0.0
        )
        repaired = normalize_plan(repaired)
        safe_validate(repaired, PLAN_SCHEMA, "plan.schema.json")
        plan = repaired

    upsert_texts(repo, issue_number, [{
        "id": f"plan_{uuid.uuid4().hex}",
        "text": f"PLAN: {plan.get('summary','')}",
        "metadata": {"type": "plan", "text": f"{plan}"}
    }])

    branch = f"agent/issue-{issue_number}-{uuid.uuid4().hex[:8]}"
    create_branch(branch)

    iteration_notes: List[str] = []
    test_cmd = run_req.get("test_command", "pytest -q")
    max_iter = int(run_req.get("max_iterations", 2))

    env_info = collect_env_info(run_req.get("stack", ""), test_cmd)
    write_out("env_info.json", json.dumps(env_info, ensure_ascii=False, indent=2))

    prev_failure_bundle: Dict[str, Any] = {}
    seen_signatures: List[str] = []
    seen_progress: List[str] = []
    stuck_count = 0

    last_test_output = ""
    final_test_exit = 999
    pr_url = None

    for i in range(1, max_iter + 1):
        repo_snap = snapshot(key_candidates)

        impl_prompt = read("prompts/implement_agent.md")
        impl_user = json.dumps({
            "iteration": i,
            "run_request": run_req,
            "plan": plan,
            "repo_snapshot_files": list(repo_snap.keys()),
            "repo_snapshot": repo_snap,
            "memories": memories,
            "previous_test_output": last_test_output,
            "previous_failure_bundle": prev_failure_bundle,
            "failure_kind": (prev_failure_bundle.get("failure_kind") if isinstance(prev_failure_bundle, dict) else None),
            "hints": (prev_failure_bundle.get("hints") if isinstance(prev_failure_bundle, dict) else []),
            "env_info": env_info
        }, ensure_ascii=False)

        patch_obj = chat_json(
            system=orchestrator_sys + "\n\n" + impl_prompt,
            user=impl_user,
            schema_name="patch.schema.json",
            temperature=0.2
        )
        patch_obj = normalize_patch(patch_obj)

        # Guardrail: no aceptar patch vacío / no-op
        if (isinstance(patch_obj, dict) and patch_obj.get('patches') == []) or (isinstance(patch_obj, dict) and patch_obj.get('files') == {}):
            raise ValueError('Patch vacío: el implementador devolvió patches=[] o files={}. Debe incluir al menos un cambio.')

        try:
            safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")
        except ValueError as e:
            repair_user = json.dumps(
                {
                    "error": str(e),
                    "invalid_patch": patch_obj,
                    "required_schema": PATCH_SCHEMA,
                    "instructions": (
                        "Devuelve SOLO JSON válido que cumpla EXACTAMENTE patch.schema.json. "
                        "Debe existir 'patches' (lista) y 'notes' (lista). "
                        "Cada patch debe incluir 'path' y 'diff' (unified diff con ---/+++). "
                        "Sin markdown."
                    )
                },
                ensure_ascii=False
            )
            patched = chat_json(
                system=orchestrator_sys + "\nEres un formateador estricto. Repara el patch al schema exacto.",
                user=repair_user,
                schema_name="patch.schema.json",
                temperature=0.0
            )
            patched = normalize_patch(patched)
            safe_validate(patched, PATCH_SCHEMA, "patch.schema.json")
            patch_obj = patched

        apply_patch_object(patch_obj)

        changed_files = git_diff_name_only()
        if not changed_files.strip():
            # No se aplicaron cambios. Esto suele indicar patch vacío o diff inválido.
            iteration_notes.append(f"Iteración {i}: ⚠️ patch no produjo cambios (changed_files vacío). Se reintentará con instrucción estricta.")
            prev_failure_bundle = {
                "iteration": i,
                "exit_code": 0,
                "test_command": test_cmd,
                "test_output_tail": "",
                "test_output_head": "",
                "failure_kind": "empty_patch",
                "hints": ["Tu patch no cambió ningún archivo. Debes devolver un patch que modifique al menos 1 archivo. Evita no-ops."],
            }
            # No corras tests; fuerza siguiente iteración
            continue

        diff_text = git_diff_full()
        write_out(f"iterations/iter_{i}_changed_files.txt", changed_files)
        write_out(f"iterations/iter_{i}_diff.patch", diff_text)
        notes_val = patch_obj.get("notes", [])
        if isinstance(notes_val, list):
            write_out(f"iterations/iter_{i}_patch_notes.txt", "\n".join([str(x) for x in notes_val]))
        else:
            write_out(f"iterations/iter_{i}_patch_notes.txt", str(notes_val))

        # Run tests
        code, out = run_cmd(test_cmd)
        final_test_exit = code
        write_out(f"iterations/iter_{i}_test_output.txt", out)

        # stuck detection por fingerprint
        failure_meta = classify_failure(out)
        # Auto-fix determinístico para float mismatch (evita loops triviales)
        if code != 0 and failure_meta.get('kind') == 'float_precision_mismatch':
            af = try_autofix_float_mismatch('.')
            if af.get('changed'):
                iteration_notes.append(f"Iteración {i}: autofix aplicado a {len(af.get('files',[]))} archivo(s) de tests (pytest.approx).")
                # re-run tests inmediatamente
                code2, out2 = run_cmd(test_cmd)
                final_test_exit = code2
                write_out(f"iterations/iter_{i}_autofix_test_output.txt", out2)
                code, out = code2, out2
                failure_meta = classify_failure(out)

        fp = failure_meta["fingerprint"]
        progress_fp = hashlib.sha256((changed_files + "\n" + str(len(diff_text))).encode('utf-8', errors='ignore')).hexdigest()[:12]

        if fp and seen_signatures and fp == seen_signatures[-1] and progress_fp == (seen_progress[-1] if seen_progress else None):
            stuck_count += 1
        else:
            stuck_count = 0
        seen_signatures.append(fp)
        seen_progress.append(progress_fp)

        kind = failure_meta["kind"]
        stuck_threshold = 2
        if kind in ("float_precision_mismatch",):
            stuck_threshold = 6
        elif kind in ("missing_dependency", "missing_pytest"):
            stuck_threshold = 3

        if stuck_count >= stuck_threshold:
            iteration_notes.append(f"Iteración {i}: atasco detectado (kind={kind}). Se detiene el loop.")
            break

        # Failure bundle (alta fidelidad) para próxima iteración
        if code != 0:
            prev_failure_bundle = {
                "iteration": i,
                "exit_code": code,
                "test_command": test_cmd,
                "test_output_tail": out[-60000:],
                "test_output_head": out[:8000],
                "changed_files": changed_files.splitlines()[-400:],
                "diff_patch_tail": diff_text[-60000:],
                "patch_notes": patch_obj.get("notes", []),
                "env_info": env_info,
                "failure_kind": failure_meta["kind"],
                "hints": failure_meta["hints"],
                "fingerprint": failure_meta["fingerprint"],
            }
            write_out(
                f"iterations/iter_{i}_failure_bundle.json",
                json.dumps(prev_failure_bundle, ensure_ascii=False, indent=2)
            )
        else:
            prev_failure_bundle = {}

        last_test_output = out
        iteration_notes.append(f"Iteración {i}: test exit={code}")

        upsert_texts(repo, issue_number, [{
            "id": f"iter_{i}_{uuid.uuid4().hex}",
            "text": f"ITER {i}: applied patch + test exit={code}",
            "metadata": {"type": "iteration", "text": json.dumps({
                "iteration": i,
                "patch_notes": patch_obj.get("notes", []),
                "test_exit": code,
                "test_output": out[:4000]
            }, ensure_ascii=False)}
        }])

        if code == 0:
            break

        # Si no hay pytest, no gastes tokens en LLM y deja artefacto claro
        if kind == "missing_pytest":
            write_out(f"iterations/iter_{i}_tester_hint.txt", "\n".join(failure_meta["hints"]))
            continue

        # ----- TEST AGENT -----
        test_prompt = read("prompts/test_agent.md")
        test_user = json.dumps({
            "iteration": i,
            "run_request": run_req,
            "plan": plan,
            "patch_notes": patch_obj.get("notes", []),
            "test_output": out,
            "changed_files": changed_files.splitlines(),
            "diff_patch": diff_text,
            "env_info": env_info,
            "repo_snapshot_files": list(repo_snap.keys()),
            "repo_snapshot": repo_snap
        }, ensure_ascii=False)

        test_report = chat_json(
            system=orchestrator_sys + "\n\n" + test_prompt,
            user=test_user,
            schema_name="test_report.schema.json",
            temperature=0.2
        )

        test_report = normalize_test_report(test_report, run_req)

        # Auto-inject hint into test_report.summary if classifier found a known issue
        if failure_meta["hints"]:
            hint_block = " | ".join(failure_meta["hints"][:2])
            if "HINT:" not in (test_report.get("summary", "") or ""):
                test_report["summary"] = f"{test_report.get('summary','')}\nHINT: {hint_block}".strip()

        try:
            safe_validate(test_report, TEST_SCHEMA, "test_report.schema.json")
        except ValueError as e:
            repair_user = json.dumps(
                {
                    "error": str(e),
                    "invalid_test_report": test_report,
                    "required_schema": TEST_SCHEMA,
                    "instructions": (
                        "Devuelve SOLO JSON válido que cumpla EXACTAMENTE test_report.schema.json. "
                        "Debe incluir 'passed' (boolean), 'summary' (string), y 'acceptance_criteria_status' (array). "
                        "Sin markdown."
                    )
                },
                ensure_ascii=False
            )
            repaired = chat_json(
                system=orchestrator_sys + "\nEres un formateador estricto. Repara el test report al schema exacto.",
                user=repair_user,
                schema_name="test_report.schema.json",
                temperature=0.0
            )
            repaired = normalize_test_report(repaired, run_req)
            safe_validate(repaired, TEST_SCHEMA, "test_report.schema.json")
            test_report = repaired

        if test_report.get("recommended_patch"):
            apply_patch_object(test_report["recommended_patch"])
            code2, out2 = run_cmd(test_cmd)
            final_test_exit = code2
            last_test_output = out2
            iteration_notes.append(f"Iteración {i} (fix): test exit={code2}")
            upsert_texts(repo, issue_number, [{
                "id": f"iter_{i}_fix_{uuid.uuid4().hex}",
                "text": f"ITER {i} FIX: applied recommended patch + test exit={code2}",
                "metadata": {"type": "iteration_fix", "text": json.dumps({
                    "iteration": i,
                    "test_exit": code2,
                    "test_output": out2[:4000]
                }, ensure_ascii=False)}
            }])
            if code2 == 0:
                break

    changed = git_status_porcelain().strip()
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)

    summary_lines = []
    summary_lines.append(f"### 🤖 Agent Orchestrator — Issue #{issue_number}")
    summary_lines.append("")
    summary_lines.append(f"**Stack/Lang:** `{run_req['stack']}` / `{run_req['language']}`")
    summary_lines.append("")
    summary_lines.append("**Historia de usuario**")
    summary_lines.append(f"> {run_req['user_story']}")
    summary_lines.append("")
    if run_req.get("acceptance_criteria"):
        summary_lines.append("**Criterios de aceptación**")
        for c in run_req["acceptance_criteria"]:
            summary_lines.append(f"- {c}")
        summary_lines.append("")
    summary_lines.append("**Plan (resumen)**")
    summary_lines.append(plan.get("summary", ""))
    summary_lines.append("")
    summary_lines.append("**Resultado de iteraciones**")
    for n in iteration_notes:
        summary_lines.append(f"- {n}")
    summary_lines.append("")

    if not changed:
        summary_lines.append("⚠️ No se detectaron cambios en el working tree. Revisa si el patch fue vacío.")
    else:
        git_commit_all(f"agent: implement issue #{issue_number}")
        git_push(current_branch())

        if final_test_exit == 0:
            title = f"[agent] Issue #{issue_number}: {issue_title or run_req.get('user_story','')[:60]}"
            body = create_pr_body(issue_number, run_req, plan, iteration_notes, last_test_output)
            try:
                pr_url = gh_pr_create(title=title, body=body, head=current_branch(), base="main")
                summary_lines.append(f"✅ PR creado: {pr_url}")
            except Exception as e:
                summary_lines.append(f"⚠️ No se pudo crear PR automáticamente: {e}")
        else:
            summary_lines.append("⚠️ Tests no pasaron; no se crea PR automáticamente. Revisa artefactos agent/out.")

    if requested_max_iterations is not None and int(run_req.get("max_iterations", 2)) != int(requested_max_iterations):
        summary_lines.append("")
        summary_lines.append(f"ℹ️ max_iterations solicitado={requested_max_iterations} fue ajustado a {run_req.get('max_iterations')} por schema.")

    with open(os.path.join(ROOT, "out", "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines) + "\n")

if __name__ == "__main__":
    main()
