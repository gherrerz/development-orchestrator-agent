import json
import os
import re
import subprocess
from typing import Any, Dict, List, Tuple

from jsonschema import validate

from agent.tools.github_tools import get_issue, create_branch, git_commit_all, gh_pr_create
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
    return {k: v for k, v in plan.items() if k in allowed}


def normalize_patch(p: Dict[str, Any]) -> Dict[str, Any]:
    """
    Patch schema expects:
      { "patches": [ { "path": "...", "diff": "..." }, ... ], "notes": [...] }

    Accept some wrappers:
      { "patch": {...} }
      { "files": {...} }  (old format) -> convert to patches with empty diffs (fallback)
    """
    if isinstance(p, dict) and "patch" in p and isinstance(p["patch"], dict):
        p = p["patch"]

    # If LLM mistakenly returns plan-like structure with "files_to_touch" etc,
    # keep only patch keys if they exist.
    if "patches" not in p and "files" in p and isinstance(p["files"], dict):
        # Convert files{} into patch-like object by writing full contents later via patch_apply fallback.
        # We'll keep it as files{} but also provide required keys so schema doesn't explode.
        p = {"patches": [], "notes": [], "files": p["files"]}

    if "patches" not in p:
        p["patches"] = []
    if "notes" not in p:
        p["notes"] = []

    # Filter patch entries to required fields
    patches = []
    for item in (p.get("patches") or []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        diff = item.get("diff")
        if isinstance(path, str) and isinstance(diff, str):
            patches.append({"path": path, "diff": diff})
    p["patches"] = patches

    return p


def normalize_test_report(tr: Dict[str, Any], run_req: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(tr, dict) and "test_report" in tr and isinstance(tr["test_report"], dict):
        tr = tr["test_report"]

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

    out: Dict[str, Any] = {
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


def stable_failure_signature(test_exit: int, test_out: str) -> str:
    """
    Compute a stable signature for stuck detection.
    Keep it robust and stack-agnostic.
    """
    txt = (test_out or "")[:4000]
    # try to capture top error line
    m = re.search(r"(?m)^(E\s+.+)$", txt)
    top = m.group(1).strip() if m else ""
    # fallback: first line with "Error" or "Exception"
    if not top:
        m2 = re.search(r"(?m)^(.{0,200}(Error|Exception).{0,200})$", txt)
        top = m2.group(1).strip() if m2 else ""
    return f"exit={test_exit}|{top[:200]}"


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

    # Prompts
    planner_prompt = open(os.path.join(BASE_DIR, "prompts", "design_agent.md"), "r", encoding="utf-8").read()
    impl_prompt = open(os.path.join(BASE_DIR, "prompts", "implement_agent.md"), "r", encoding="utf-8").read()
    test_prompt = open(os.path.join(BASE_DIR, "prompts", "test_agent.md"), "r", encoding="utf-8").read()

    # Planner -> Plan (MUST pass schema_name)
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
        }, ensure_ascii=False),
        schema_name="plan.schema.json",
    )
    plan = normalize_plan(plan)
    safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")

    max_iterations = int(run_req.get("max_iterations", 2))
    base_branch = "main"
    branch_name = f"agent/issue-{issue_number}"
    create_branch(branch_name)

    iteration_notes: List[str] = []
    last_sig = ""
    stuck_count = 0

    os.makedirs("agent/out", exist_ok=True)

    for i in range(1, max_iterations + 1):
        prev_test_output = ""
        if os.path.exists("agent/out/last_test_output.txt"):
            prev_test_output = open("agent/out/last_test_output.txt", "r", encoding="utf-8", errors="replace").read()

        prev_hints: List[str] = []
        if os.path.exists("agent/out/failure_hints.json"):
            try:
                prev_hints = json.loads(open("agent/out/failure_hints.json", "r", encoding="utf-8").read())
            except Exception:
                prev_hints = []

        # Implement -> Patch (MUST pass schema_name)
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
        patch_obj = normalize_patch(patch_obj)
        safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")

        # Apply patch
        apply_patch_object(patch_obj)

        # Changed files list (git)
        _, changed = run_cmd("git diff --name-only")
        changed = changed.strip()
        write_out(f"agent/out/iter_{i}_changed_files.txt", changed)

        # If no changes, don't burn iteration with same state
        if not changed:
            iteration_notes.append(f"Iteración {i}: ⚠️ patch no produjo cambios reales. Reintentando.")
            write_out("agent/out/failure_hints.json", json.dumps([
                "El patch no cambió ningún archivo. Debes modificar al menos 1 archivo real.",
                "Si el diff unificado falla, usa formato files{} para escribir contenidos completos."
            ], ensure_ascii=False))
            continue

        # Refresh snapshot for changed files (higher-fidelity loop)
        changed_paths = [p.strip() for p in changed.splitlines() if p.strip()]
        if changed_paths:
            # snapshot expects paths like ./...
            snap_paths = []
            for pth in changed_paths[:25]:
                if pth.startswith("./"):
                    snap_paths.append(pth)
                else:
                    snap_paths.append("./" + pth)
            try:
                repo_snap = snapshot(list(dict.fromkeys(key_candidates + snap_paths))[:80])
            except Exception:
                pass

        # Run tests
        test_cmd = run_req.get("test_command") or ""
        test_exit, test_out = run_cmd(test_cmd)
        write_out("agent/out/last_test_output.txt", test_out)

        # Failure hints from heuristic classifier (module)
        try:
            meta = fh_classify_failure(
                test_out,
                language=language,
                stack=str(run_req.get("stack", "")),
                test_command=str(run_req.get("test_command", "")),
            )
        except TypeError:
            # if failure_hints.classify_failure has simpler signature
            meta = fh_classify_failure(test_out, language=language)

        hints = summarize_hints(meta) if meta else []
        if test_exit != 0 and hints:
            write_out("agent/out/failure_hints.json", json.dumps(hints, ensure_ascii=False))

        iteration_notes.append(f"Iteración {i}: test exit={test_exit}")

        # Tester agent report (MUST pass schema_name)
        test_report = chat_json(
            system=test_prompt,
            user=json.dumps({
                "stack": run_req.get("stack"),
                "language": run_req.get("language"),
                "test_command": test_cmd,
                "test_exit_code": test_exit,
                "test_output": test_out,
                "acceptance_criteria": run_req.get("acceptance_criteria", []),
                "failure_hints": hints,
            }, ensure_ascii=False),
            schema_name="test_report.schema.json",
        )
        test_report = normalize_test_report(test_report, run_req)
        safe_validate(test_report, TEST_SCHEMA, "test_report.schema.json")

        # persist hints from tester
        if test_report.get("failure_hints"):
            write_out("agent/out/failure_hints.json", json.dumps(test_report["failure_hints"], ensure_ascii=False))

        # stuck detection: use stable signature + changed list
        new_sig = stable_failure_signature(test_exit, test_out)

        if test_exit != 0 and should_count_as_stuck(last_sig, new_sig, changed):
            stuck_count += 1
        else:
            stuck_count = 0

        last_sig = new_sig

        if stuck_count >= 2:
            iteration_notes.append(f"Iteración {i}: atasco detectado (mismo error + mismos cambios). Se detiene el loop.")
            break

        if bool(test_report.get("passed")):
            git_commit_all(f"agent: implement issue #{issue_number}")
            pr_url = gh_pr_create(
                title=f"[agent] Issue #{issue_number}: {issue_title}",
                body=f"### 🤖 Agent Orchestrator — Issue #{issue_number}\n\nPlan:\n```json\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n```",
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
        "",
    ] + [f"- {n}" for n in iteration_notes]

    write_out("agent/out/summary.md", "\n".join(summary_lines))


if __name__ == "__main__":
    main()
