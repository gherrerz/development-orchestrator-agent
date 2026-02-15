import json
import os
import re
import subprocess
from typing import Any, Dict, List, Tuple
import shlex
import hashlib

from agent.stacks.registry import resolve_stack_spec, load_catalog
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
    return f"T{i}-{h}"


def _repair_tasks(tasks_val: Any) -> List[Dict[str, str]]:
    """
    Enforce schema for tasks[] items:
      {id,title,description} only (strings)
    - If missing id/title, derive them.
    - If extra fields exist, fold them into description so info isn't lost.
    """
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

        if not tid:
            tid = _mk_task_id(idx, title, desc)

        repaired.append({"id": tid, "title": title, "description": desc})

    return repaired


def normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Robust plan normalization:
    - filter keys not in schema
    - coerce types for strict fields
    - repair tasks[] items to comply required fields and forbid extras
    """
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

    if "patches" not in p and "files" in p and isinstance(p["files"], dict):
        p = {"patches": [], "notes": [], "files": p["files"]}

    if "patches" not in p:
        p["patches"] = []
    if "notes" not in p:
        p["notes"] = []

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

    if all(k in tr for k in ("passed", "summary")):
        passed = bool(tr.get("passed"))
        summary = str(tr.get("summary") or "")
    else:
        passed = False
        summary = str(tr)[:1000]

    ac = run_req.get("acceptance_criteria", []) or []
    ac_list = [{"criteria": c, "met": passed, "evidence": summary[:400]} for c in ac]

    out: Dict[str, Any] = {
        "passed": passed,
        "summary": summary,
        "failure_hints": tr.get("failure_hints", []),
        "acceptance_criteria_status": ac_list,
    }
    if "recommended_patch" in tr:
        out["recommended_patch"] = tr["recommended_patch"]
    return out


def _has_shell_metachars(cmd: str) -> bool:
    return bool(re.search(r"[;&|`$><\n\r]", cmd or ""))


def is_safe_test_command(cmd: str, allowed_prefixes: List[str]) -> bool:
    cmd = (cmd or "").strip()
    if not cmd:
        return True
    if _has_shell_metachars(cmd):
        return False
    normalized = re.sub(r"\s+", " ", cmd).strip().lower()
    allowed = [re.sub(r"\s+", " ", a).strip().lower() for a in (allowed_prefixes or [])]
    return any(normalized.startswith(a) for a in allowed)


def run_cmd(cmd: str) -> Tuple[int, str]:
    args = shlex.split(cmd)
    p = subprocess.run(args, text=True, capture_output=True)
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
    md.append(f"## 🤖 Agent 결과\n")
    md.append(f"**Issue:** {issue_title}\n")
    if pr_url:
        md.append(f"**PR:** {pr_url}\n")
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
    """
    Enterprise fallback: one-shot schema repair using LLM, forced to plan.schema.json.
    If the model still fails schema validation, caller aborts with clear error.
    """
    repair_prompt = _load_prompt("repair_plan_agent.md")

    # Persist invalid input + error for auditability
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
        schema_name="plan.schema.json",  # hard enforcement
    )

    # Normalize (still) and return
    repaired = normalize_plan(repaired)

    # Persist repaired plan for auditability
    write_out("agent/out/plan_repaired.json", json.dumps(repaired, ensure_ascii=False, indent=2))

    return repaired


def main() -> None:
    repo = os.environ["REPO"]
    issue_number = str(os.environ["ISSUE_NUMBER"])
    comment_body = os.environ.get("COMMENT_BODY", "")

    issue = get_issue(repo, issue_number)
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "") or ""

    run_req = extract_json_from_comment(comment_body)

    catalog = load_catalog()
    spec = resolve_stack_spec(run_req, repo_root=".", catalog=catalog)

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

    # ---------------------------
    # Planner -> Plan (with enterprise repair fallback, max 1)
    # ---------------------------
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
        # one-shot repair
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
            # Abort clearly: enterprise policy
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
    create_branch(branch_name, base_branch)

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

        patch_obj = normalize_patch(patch_obj)
        safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")

        apply_patch_object(patch_obj)

        _, changed = run_cmd("git diff --name-only")
        changed = changed.strip()
        write_out(f"agent/out/iter_{i}_changed_files.txt", changed)

        if not changed:
            iteration_notes.append(f"Iteración {i}: sin cambios detectados (skip)")
            continue

        git_commit_all(f"agent: implement issue {issue_number} (iter {i})")

        # RUN TESTS (SIN SHELL)
        test_cmd = run_req.get("test_command") or ""
        test_exit, test_out = run_cmd(test_cmd)
        write_out("agent/out/last_test_output.txt", test_out)

        # FAILURE HINTS
        try:
            meta = fh_classify_failure(
                test_out,
                language=language,
                stack=str(run_req.get("stack") or ""),
            )
            hints = summarize_hints(meta)
            write_out(
                "agent/out/failure_hints.json",
                json.dumps(hints, ensure_ascii=False, indent=2)
            )
        except Exception:
            hints = []

        # Correct pinecone upsert after tests
        pine_upsert_event(
            repo,
            issue_number,
            "iteration",
            f"iter={i}\nchanged=\n{changed}\n\nexit={test_exit}\n\n{test_out[:4000]}",
            {
                "iteration": i,
                "exit": int(test_exit),
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

        iteration_notes.append(f"Iteración {i}: test exit={test_exit}")

        # Optional: stuck detection (kept, but not aborting hard here)
        failure_sig = stable_failure_signature(test_exit, test_out)
        if should_count_as_stuck(last_failure_sig, failure_sig):
            stuck_count += 1
        else:
            stuck_count = 0
        last_failure_sig = failure_sig

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
