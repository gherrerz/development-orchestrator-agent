import os
import re
import json
import uuid
import textwrap
import subprocess
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

def read(path: str) -> str:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()

def load_schema(name: str) -> Dict[str, Any]:
    with open(os.path.join(ROOT, "schemas", name), "r", encoding="utf-8") as f:
        return json.load(f)

RUN_SCHEMA = load_schema("run_request.schema.json")
PLAN_SCHEMA = load_schema("plan.schema.json")
PATCH_SCHEMA = load_schema("patch.schema.json")
TEST_SCHEMA = load_schema("test_report.schema.json")

def extract_json_from_comment(comment_body: str) -> Dict[str, Any]:
    """
    Expects: /agent run { ...json... }
    """
    m = re.search(r"/agent\s+run\s+(\{.*\})\s*$", comment_body, re.DOTALL)
    if not m:
        raise ValueError("No se encontró JSON. Usa: /agent run { ... }")
    raw = m.group(1)
    return json.loads(raw)

def safe_validate(obj: Dict[str, Any], schema: Dict[str, Any], schema_name: str) -> None:
    try:
        validate(instance=obj, schema=schema)
    except ValidationError as e:
        raise ValueError(f"JSON inválido para {schema_name}: {e.message}")

def run_cmd(cmd: str) -> Tuple[int, str]:
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode, out.strip()

def compact_memories(mem_matches: List[Dict[str, Any]], max_items: int = 8) -> List[str]:
    texts = []
    for m in mem_matches[:max_items]:
        md = m.get("metadata", {})
        t = md.get("text", "")
        if t:
            texts.append(f"- (score={m['score']:.3f}) {t[:800]}")
    return texts

def main():
    repo = os.environ["REPO"]
    issue_number = str(os.environ["ISSUE_NUMBER"])
    comment_body = os.environ.get("COMMENT_BODY", "")

    issue = get_issue(repo, issue_number)
    issue_title = issue.get("title", "")
    issue_body = issue.get("body", "") or ""

    run_req = extract_json_from_comment(comment_body)
    safe_validate(run_req, RUN_SCHEMA, "run_request.schema.json")

    # Build initial query for memory
    mem_query_text = f"{run_req['stack']} | {run_req['language']} | {run_req['user_story']} | {issue_title}"
    mem_matches = pine_query(repo, issue_number, mem_query_text, top_k=8)
    memories = compact_memories(mem_matches)

    # Repo snapshot: take a focused sample of key files
    files = list_files(".")
    key_candidates = [p for p in files if any(p.endswith(suf) for suf in [
        "pyproject.toml", "requirements.txt", "setup.cfg", "setup.py", "README.md",
        "Makefile", "pytest.ini", "tox.ini", "ruff.toml"
    ])]
    # include top-level python packages and tests
    key_candidates += [p for p in files if p.startswith("./tests") or p.endswith("__init__.py")]
    key_candidates = list(dict.fromkeys(key_candidates))[:40]  # unique + cap
    repo_snap = snapshot(key_candidates)

    # ----- DESIGN AGENT -----
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
    safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")

    # Persist plan to memory
    upsert_texts(repo, issue_number, [{
        "id": f"plan_{uuid.uuid4().hex}",
        "text": f"PLAN: {plan.get('summary','')}\nTEST: {plan.get('test_strategy','')}",
        "metadata": {"type": "plan", "text": f"{plan}"}
    }])

    # Create branch
    branch = f"agent/issue-{issue_number}-{uuid.uuid4().hex[:8]}"
    create_branch(branch)

    iteration_notes: List[str] = []
    test_cmd = run_req.get("test_command", "pytest -q")
    max_iter = int(run_req.get("max_iterations", 2))

    last_test_output = ""
    pr_url = None

    for i in range(1, max_iter + 1):
        # Refresh focused snapshot (optional: could include more files after changes)
        # Keep it light for prompt size
        repo_snap = snapshot(key_candidates)

        # ----- IMPLEMENT AGENT -----
        impl_prompt = read("prompts/implement_agent.md")
        impl_user = json.dumps({
            "iteration": i,
            "run_request": run_req,
            "plan": plan,
            "repo_snapshot_files": list(repo_snap.keys()),
            "repo_snapshot": repo_snap,
            "memories": memories,
            "previous_test_output": last_test_output
        }, ensure_ascii=False)

        patch_obj = chat_json(
            system=orchestrator_sys + "\n\n" + impl_prompt,
            user=impl_user,
            schema_name="patch.schema.json",
            temperature=0.2
        )
        safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")

        # Apply patch
        apply_patch_object(patch_obj)

        # Run tests
        code, out = run_cmd(test_cmd)
        last_test_output = out

        iteration_notes.append(f"Iteración {i}: test exit={code}")

        # Persist patch + tests to memory
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

        # ----- TEST AGENT (diagnose and propose fix) -----
        test_prompt = read("prompts/test_agent.md")
        test_user = json.dumps({
            "iteration": i,
            "run_request": run_req,
            "plan": plan,
            "patch_notes": patch_obj.get("notes", []),
            "test_output": out,
            "repo_snapshot_files": list(repo_snap.keys()),
            "repo_snapshot": repo_snap
        }, ensure_ascii=False)

        test_report = chat_json(
            system=orchestrator_sys + "\n\n" + test_prompt,
            user=test_user,
            schema_name="test_report.schema.json",
            temperature=0.2
        )
        safe_validate(test_report, TEST_SCHEMA, "test_report.schema.json")

        if test_report.get("recommended_patch"):
            # apply recommended patch and re-run tests once within same iteration
            apply_patch_object(test_report["recommended_patch"])
            code2, out2 = run_cmd(test_cmd)
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

    # If changes exist, commit + push + PR
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
        git_push(branch)

        pr_body = "\n".join(summary_lines) + "\n\n" + "Logs (último test output, truncado):\n```\n" + (last_test_output[:4000] or "") + "\n```"
        pr_url = gh_pr_create(
            title=f"[agent] Issue #{issue_number}: {issue_title[:80]}",
            body=pr_body,
            head=branch,
            base="main"
        )
        summary_lines.append(f"✅ PR creado: {pr_url}")

    with open(os.path.join(ROOT, "out", "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines).strip())

if __name__ == "__main__":
    main()
