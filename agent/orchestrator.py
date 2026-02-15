import json
import os
import re
import subprocess
from typing import Any, Dict, List, Tuple

from jsonschema import validate

from agent.tools.github_tools import get_issue
from agent.tools.github_tools import create_branch, git_commit_all
from agent.tools.github_tools import gh_pr_create
from agent.tools.llm import chat_json
from agent.tools.patch_apply import apply_patch_object
from agent.tools.repo_scan import list_files, snapshot
from agent.tools.failure_hints import classify_failure, summarize_hints, should_count_as_stuck

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
        # keep the original message concise but clear
        msg = getattr(e, "message", str(e))
        raise ValueError(f"JSON inválido para {schema_name}: {msg}") from e

def extract_json_from_comment(body: str) -> Dict[str, Any]:
    m = re.search(r"/agent\s+run\s*(\{.*\})\s*$", (body or "").strip(), re.DOTALL)
    if not m:
        m = re.search(r"(\{.*\})", (body or "").strip(), re.DOTALL)
    if not m:
        raise ValueError("No se encontró JSON. Usa: /agent run { ... }")
    return json.loads(m.group(1))

def apply_stack_defaults(run_req: Dict[str, Any]) -> Dict[str, Any]:
    # Default test command by language (agnostic)
    lang = (run_req.get("language") or "").lower().strip()
    stack = (run_req.get("stack") or "").strip()

    if not lang and stack:
        if stack.startswith("python-"):
            lang = "python"
        elif stack.startswith("java-"):
            lang = "java"
        elif stack.startswith("node-"):
            lang = "javascript"
        elif stack.startswith("dotnet-"):
            lang = "dotnet"
        elif stack.startswith("go-"):
            lang = "go"

    if lang and not run_req.get("test_command"):
        if lang == "python":
            run_req["test_command"] = "pytest -q"
        elif lang in ("javascript", "typescript"):
            run_req["test_command"] = "npm test"
        elif lang == "java":
            run_req["test_command"] = "mvn -q test"
        elif lang in ("dotnet", "csharp"):
            run_req["test_command"] = "dotnet test"
        elif lang == "go":
            run_req["test_command"] = "go test ./..."

    run_req["language"] = lang or (run_req.get("language") or "")
    return run_req

def normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Some LLMs accidentally include patch fields (patches/notes) in Plan output.
    Plan schema has additionalProperties:false, so we must filter.
    """
    allowed = set((PLAN_SCHEMA.get("properties") or {}).keys())
    filtered = {k: v for k, v in plan.items() if k in allowed}
    return filtered

def normalize_patch(p: Dict[str, Any]) -> Dict[str, Any]:
    # Accept wrapper forms
    if isinstance(p, dict) and "patch" in p and isinstance(p["patch"], dict):
        p = p["patch"]

    # Ensure notes
    if "notes" not in p:
        p["notes"] = []

    return p

def normalize_test_report(tr: Dict[str, Any], run_req: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(tr, dict) and "test_report" in tr and isinstance(tr["test_report"], dict):
        tr = tr["test_report"]

    # already correct
    if all(k in tr for k in ("passed", "summary", "acceptance_criteria_status")):
        if "failure_hints" not in tr:
            tr["failure_hints"] = []
        return tr

    passed = bool(tr.get("passed", tr.get("tests_passed", False)))
    summary = tr.get("summary") or tr.get("test_summary") or "Resultado de pruebas no especificado."

    criteria = run_req.get("acceptance_criteria") or []
    evidence_base = tr.get("criteria_verification") or tr.get("summary") or tr.get("test_summary") or ""
    if not isinstance(evidence_base, str):
        evidence_base = str(evidence_base)

    ac_list = []
    if criteria:
        for c in criteria:
            ac_list.append({"criterion": c, "met": passed, "evidence": evidence_base[:400]})
    else:
        ac_list = [{"criterion": "N/A", "met": passed, "evidence": summary[:400]}]

    out = {
        "passed": passed,
        "summary": summary,
        "failure_hints": tr.get("failure_hints", []),
        "acceptance_criteria_status": ac_list,
    }
    if "recommended_patch" in tr:
        out["recommended_patch"] = tr["recommended_patch"]
    return out

def run_cmd(cmd: str) -> Tuple[int, str]:
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    return p.returncode, out

def classify_failure(test_output: str, language: str) -> Dict[str, Any]:
    """
    Multi-stack heuristic classifier that outputs:
      kind: string
      hints: [string]
    """
    out = test_output or ""
    hints: List[str] = []

    # Float mismatch patterns (generic)
    if re.search(r"\d+\.\d+\s*!=\s*\d+\.\d+", out) or "assert" in out.lower() and "close" in out.lower():
        hints.append("Posible mismatch de precisión (float). Evita comparaciones exactas.")
        if language == "python":
            hints.append("Pytest: usa pytest.approx(expected, abs=0.01) o redondeo round(x,2) / Decimal.quantize.")
        elif language in ("javascript", "typescript"):
            hints.append("Jest: usa toBeCloseTo(expected, precision) o tolerancia.")
        elif language == "java":
            hints.append("JUnit: assertEquals(expected, actual, delta).")
        elif language in ("dotnet", "csharp"):
            hints.append(".NET: usa tolerancia/precision en asserts (Assert.Equal con precision o delta).")
        elif language == "go":
            hints.append("Go: compara con tolerancia (abs(a-b) < eps).")
        return {"kind": "float_precision_mismatch", "hints": hints}

    # Missing dependency/module
    if "ModuleNotFoundError" in out or "No module named" in out:
        hints.append("Falta dependencia o packaging incorrecto. Verifica instalación y rutas.")
        if language == "python":
            hints.append("Python: revisa requirements/pyproject; asegúrate de instalar deps antes de pytest.")
        return {"kind": "missing_module", "hints": hints}

    if "Cannot find module" in out:
        hints.append("Node: falta dependencia o path incorrecto. Ejecuta npm install / revisa package.json.")
        return {"kind": "missing_module", "hints": hints}

    if "Could not find artifact" in out or "BUILD FAILURE" in out:
        hints.append("Java/Maven: dependency resolution failure. Revisa pom.xml y repositorios.")
        return {"kind": "build_failure", "hints": hints}

    if "NU" in out and "error" in out.lower():
        hints.append(".NET/NuGet: fallo de restore. Revisa packages y fuentes.")
        return {"kind": "build_failure", "hints": hints}

    return {"kind": "unknown", "hints": []}

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

def write_out(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(content)

def main() -> None:
    repo = os.environ["REPO"]
    issue_number = str(os.environ["ISSUE_NUMBER"])
    comment_body = os.environ.get("COMMENT_BODY", "")

    issue = get_issue(repo, issue_number)
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "") or ""

    run_req = extract_json_from_comment(comment_body)
    run_req = apply_stack_defaults(run_req)
    safe_validate(run_req, RUN_SCHEMA, "run_request.schema.json")

    language = (run_req.get("language") or "").lower().strip()

    # Memory (safe degradation)
    memories = ""
    if pine_query:
        try:
            mem_query_text = f"{run_req.get('stack')} | {run_req.get('language')} | {run_req.get('user_story')} | {issue_title}"
            mem_matches = pine_query(repo, issue_number, mem_query_text, top_k=8)
            memories = compact_memories(mem_matches)
        except Exception as e:
            memories = f"(memory disabled: {e})"

    # Repo snapshot
    files = list_files(".")
    key_candidates = [p for p in files if any(p.endswith(suf) for suf in [
        "pyproject.toml", "requirements.txt", "package.json", "package-lock.json",
        "pom.xml", "build.gradle", "gradlew", "go.mod",
        ".sln", ".csproj",
        "README.md", "Makefile", "pytest.ini", "tox.ini"
    ])]
    key_candidates = list(dict.fromkeys(key_candidates))[:60]
    repo_snap = snapshot(key_candidates)

    # Planner
    planner_prompt = open(os.path.join(BASE_DIR, "prompts", "plan_agent.md"), "r", encoding="utf-8").read()
    plan = chat_json(
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
        }, ensure_ascii=False)
    )
    plan = normalize_plan(plan)
    safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")

    max_iterations = int(run_req.get("max_iterations", 2))
    base_branch = "main"
    branch_name = f"agent/issue-{issue_number}"
    create_branch(branch_name)

    impl_prompt = open(os.path.join(BASE_DIR, "prompts", "implement_agent.md"), "r", encoding="utf-8").read()
    test_prompt = open(os.path.join(BASE_DIR, "prompts", "test_agent.md"), "r", encoding="utf-8").read()

    iteration_notes: List[str] = []
    last_failure_sig = ""
    stuck_count = 0

    for i in range(1, max_iterations + 1):
        # Run tests BEFORE changes? (optional) -> here we implement then test
        # Implement
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
                "previous_test_output": (open("agent/out/last_test_output.txt").read() if os.path.exists("agent/out/last_test_output.txt") else ""),
                "failure_hints": (json.loads(open("agent/out/failure_hints.json").read()) if os.path.exists("agent/out/failure_hints.json") else [])
            }, ensure_ascii=False)
        )
        patch_obj = normalize_patch(patch_obj)
        safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")

        # Apply patch
        apply_patch_object(patch_obj)

        # Determine changes
        code, diff_out = run_cmd("git diff --name-only")
        changed = diff_out.strip()
        write_out(f"agent/out/iter_{i}_changed_files.txt", changed)

        if not changed:
            iteration_notes.append(f"Iteración {i}: ⚠️ patch no produjo cambios reales. Reintentando.")
            # force hints
            write_out("agent/out/failure_hints.json", json.dumps([
                "El patch no cambió ningún archivo. Debes modificar al menos 1 archivo real.",
                "Usa formato files{} si los diffs fallan."
            ], ensure_ascii=False))
            continue

        # Run tests
        test_cmd = run_req.get("test_command") or ""
        test_exit, test_out = run_cmd(test_cmd)
        write_out("agent/out/last_test_output.txt", test_out)

        # classify failure + hints
        meta = classify_failure(
            test_output=test_out,
            language=language,
            stack=str(run_req.get("stack", "")),
            test_command=str(run_req.get("test_command", "")),
        )
        hints = summarize_hints(meta)

        if test_exit != 0 and hints:
            write_out("agent/out/failure_hints.json", json.dumps(hints, ensure_ascii=False))

        iteration_notes.append(f"Iteración {i}: test exit={test_exit}")

        # Tester agent produces structured report
        test_report = chat_json(
            system=test_prompt,
            user=json.dumps({
                "stack": run_req.get("stack"),
                "language": run_req.get("language"),
                "test_command": test_cmd,
                "test_exit_code": test_exit,
                "test_output": test_out,
                "acceptance_criteria": run_req.get("acceptance_criteria", []),
                "failure_hints": failure_meta.get("hints", []),
            }, ensure_ascii=False)
        )
        test_report = normalize_test_report(test_report, run_req)
        safe_validate(test_report, TEST_SCHEMA, "test_report.schema.json")

        # store hints if provided
        if test_report.get("failure_hints"):
            write_out("agent/out/failure_hints.json", json.dumps(test_report["failure_hints"], ensure_ascii=False))

        # stuck detection: use signature of last failure + changed files (progress)
        new_sig = meta.signature

        if test_exit != 0 and should_count_as_stuck(last_failure_sig, new_sig, changed):
            stuck_count += 1
        else:
            stuck_count = 0

        last_failure_sig = new_sig

        if stuck_count >= 2:
            iteration_notes.append(f"Iteración {i}: atasco detectado (mismo error + mismos cambios). Se detiene el loop.")
            break

        if test_report.get("passed"):
            # commit and open PR (only if passed)
            git_commit_all(f"agent: implement issue #{issue_number}")
            pr_url = gh_pr_create(
                title=f"[agent] Issue #{issue_number}: {issue_title}",
                body=f"### 🤖 Agent Orchestrator — Issue #{issue_number}\n\n{json.dumps(plan, ensure_ascii=False, indent=2)}",
                head=branch_name,
                base=base_branch,
            )
            iteration_notes.append(f"✅ Tests pasaron; PR creado: {pr_url}")
            break

    # Summary
    summary_lines = [
        f"🤖 Agent Orchestrator — Issue #{issue_number}",
        f"Stack/Lang: {run_req.get('stack')} / {run_req.get('language')}",
        "",
        "Historia de usuario",
        "",
        run_req.get("user_story", ""),
        "",
        "Criterios de aceptación",
        "",
    ] + [f"- {c}" for c in (run_req.get("acceptance_criteria") or [])] + [
        "",
        "Plan (resumen)",
        plan.get("summary", ""),
        "",
        "Resultado de iteraciones",
        ""
    ] + [f"- {n}" for n in iteration_notes]

    os.makedirs("agent/out", exist_ok=True)
    write_out("agent/out/summary.md", "\n".join(summary_lines))

if __name__ == "__main__":
    main()
