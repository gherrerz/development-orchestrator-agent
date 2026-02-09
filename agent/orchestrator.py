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
    get_issue, create_branch, git_status_porcelain, git_commit_all, git_push,
    gh_pr_create, gh_issue_comment, current_branch
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

def normalize_plan(plan_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fix common model deviations:
    - unwrap {"plan": {...}}
    - convert {"steps": [...]} to schema fields
    """
    if isinstance(plan_obj, dict) and "plan" in plan_obj and isinstance(plan_obj["plan"], dict):
        plan_obj = plan_obj["plan"]

    # If model returned "steps" with richer fields, map them.
    if "steps" in plan_obj and isinstance(plan_obj["steps"], list):
        steps = plan_obj["steps"]

        # Build tasks from steps
        tasks = []
        files_to_touch = set()
        test_strategy = plan_obj.get("test_strategy") or ""

        for idx, s in enumerate(steps, start=1):
            desc = s.get("step") or s.get("description") or f"Step {idx}"
            tasks.append({
                "id": f"S{idx}",
                "title": (desc[:60] + "...") if len(desc) > 60 else desc,
                "description": desc
            })

            for f in (s.get("files_to_modify") or []):
                files_to_touch.add(str(f))
            for f in (s.get("files_to_create") or []):
                files_to_touch.add(str(f))

            if not test_strategy and s.get("tests_strategy"):
                test_strategy = s["tests_strategy"]

            # carry over risks/assumptions if present inside step
            if "risks" in s and "risks" not in plan_obj:
                plan_obj["risks"] = s.get("risks", [])
            if "assumptions" in s and "assumptions" not in plan_obj:
                plan_obj["assumptions"] = s.get("assumptions", [])

        plan_obj["tasks"] = plan_obj.get("tasks") or tasks
        plan_obj["files_to_touch"] = plan_obj.get("files_to_touch") or sorted(files_to_touch)
        plan_obj["test_strategy"] = plan_obj.get("test_strategy") or test_strategy or "Agregar/ajustar tests y ejecutar el comando de tests indicado."

        # Remove non-schema field
        plan_obj.pop("steps", None)

    return plan_obj

def normalize_patch(patch_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Supports two model outputs:
    1) Expected schema: {"patches":[{"path","diff"}], "notes":[...]}
    2) Alternate: {"files": {"path": {"content": "..."} , ...}, "notes":[...]}
       -> convert to unified diffs that 'git apply' can consume.
    """
    # already correct
    if isinstance(patch_obj, dict) and "patches" in patch_obj:
        if "notes" not in patch_obj:
            patch_obj["notes"] = []
        return patch_obj

    # unwrap common wrapper
    if isinstance(patch_obj, dict) and "patch" in patch_obj and isinstance(patch_obj["patch"], dict):
        patch_obj = patch_obj["patch"]

    # alternate files format
    if isinstance(patch_obj, dict) and "files" in patch_obj and isinstance(patch_obj["files"], dict):
        files = patch_obj["files"]
        notes = patch_obj.get("notes", [])

        patches = []
        for path, spec in files.items():
            # support {"content": "..."} or direct string
            new_content = spec.get("content") if isinstance(spec, dict) else str(spec)

            # read old content if exists
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
                existed = True
            except FileNotFoundError:
                old_content = ""
                existed = False

            # build unified diff with python difflib
            import difflib
            old_lines = old_content.splitlines(keepends=True)
            new_lines = new_content.splitlines(keepends=True)

            a_path = f"a/{path}"
            b_path = f"b/{path}"
            fromfile = a_path if existed else "/dev/null"
            tofile = b_path

            diff = "".join(difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=fromfile,
                tofile=tofile,
                lineterm=""
            ))

            # difflib returns lines without trailing \n when lineterm="" → normalize
            if diff and not diff.endswith("\n"):
                diff += "\n"

            # git apply requires the ---/+++ headers; difflib provides them
            patches.append({"path": path, "diff": diff})

        return {"patches": patches, "notes": notes if isinstance(notes, list) else []}

    return patch_obj

def normalize_test_report(tr: Dict[str, Any], run_req: Dict[str, Any]) -> Dict[str, Any]:
    # unwrap common wrapper
    if isinstance(tr, dict) and "test_report" in tr and isinstance(tr["test_report"], dict):
        tr = tr["test_report"]

    # already correct
    if "passed" in tr and "summary" in tr and "acceptance_criteria_status" in tr:
        return tr

    # map common alternative keys
    passed = None
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
        # if model provided criteria_verification text, use as evidence
        evidence_base = tr.get("criteria_verification") or tr.get("summary") or tr.get("test_summary") or ""
        for c in criteria:
            ac_list.append({"criterion": c, "met": passed, "evidence": evidence_base[:400]})
    else:
        # no acceptance criteria provided
        ac_list = [{"criterion": "N/A", "met": passed, "evidence": summary[:400]}]

    out = {
        "passed": passed,
        "summary": summary,
        "acceptance_criteria_status": ac_list
    }

    # carry recommended_patch if present (and it may need normalize_patch elsewhere)
    if "recommended_patch" in tr:
        out["recommended_patch"] = tr["recommended_patch"]

    return out

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
    # Normaliza errores comunes (plan wrapper, steps, etc.)
    plan = normalize_plan(plan)

    try:
        safe_validate(plan, PLAN_SCHEMA, "plan.schema.json")

    except ValueError as e:
        # 🔁 REPAIR STEP: pedir explícitamente al LLM que repare el JSON
        repair_user = json.dumps(
            {
                "error": str(e),
                "invalid_plan": plan,
                "required_schema": PLAN_SCHEMA,
                "instructions": (
                    "Repara el JSON para que cumpla EXACTAMENTE el schema. "
                    "No agregues wrappers como 'plan'. "
                    "No incluyas markdown. "
                    "Devuelve solo el objeto JSON válido."
                )
            },
            ensure_ascii=False
        )

        repaired = chat_json(
            system=orchestrator_sys
            + "\nEres un formateador estricto. Repara el JSON al schema exacto.",
            user=repair_user,
            schema_name="plan.schema.json",
            temperature=0.0
        )

        repaired = normalize_plan(repaired)
        safe_validate(repaired, PLAN_SCHEMA, "plan.schema.json")
        plan = repaired

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
    final_test_exit: int = 999  # status final de tests (0 = OK)

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
        patch_obj = normalize_patch(patch_obj)

        try:
            safe_validate(patch_obj, PATCH_SCHEMA, "patch.schema.json")
        except ValueError as e:
            # (opcional) reparo automático: re-preguntar al LLM
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

        # Run tests
        code, out = run_cmd(test_cmd)
        if "pytest: not found" in out or "/bin/sh: 1: pytest: not found" in out:
            # reporta en formato schema y salta test agent
            test_report = {
                "passed": False,
                "summary": "No se pudieron ejecutar tests: pytest no está instalado en el runner. Instala pytest o ajusta test_command.",
                "acceptance_criteria_status": [
                    {"criterion": c, "met": False, "evidence": "pytest no disponible"} for c in (run_req.get("acceptance_criteria") or ["Incluir test unitarios"])
                ]
            }
            # y opcionalmente: break o continuar a repair

        last_test_output = out
        final_test_exit = code

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

        test_report = normalize_test_report(test_report, run_req)

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
            # apply recommended patch and re-run tests once within same iteration
            apply_patch_object(test_report["recommended_patch"])
            code2, out2 = run_cmd(test_cmd)
            last_test_output = out2
            final_test_exit = code2
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
        # Siempre persistimos la rama para no perder cambios
        git_commit_all(f"agent: implement issue #{issue_number}")
        git_push(branch)

        tests_ok = (final_test_exit == 0)

        if not tests_ok:
            msg = (
                f"⚠️ **No se creó PR** porque los tests no pasaron.\n\n"
                f"- Branch: `{branch}`\n"
                f"- Último exit code: `{final_test_exit}`\n\n"
                f"**Salida (truncada):**\n```text\n{(last_test_output or '')[:2500]}\n```\n"
            )
            try:
                gh_issue_comment(str(issue_number), msg)
            except Exception:
                print(msg)

            summary_lines.append(f"⚠️ No se creó PR porque los tests fallaron. Branch: {branch}")
        else:
            pr_body = (
                "\n".join(summary_lines)
                + "\n\n"
                + "Logs (último test output, truncado):\n```\n"
                + (last_test_output[:4000] or "")
                + "\n```"
            )
            pr_url = gh_pr_create(
                title=f"[agent] Issue #{issue_number}: {issue_title[:80]}",
                body=pr_body,
                head=branch,
                base="main"
            )
            summary_lines.append(f"✅ PR creado: {pr_url}")
            try:
                gh_issue_comment(str(issue_number), f"✅ PR creado: {pr_url}")
            except Exception:
                pass


    with open(os.path.join(ROOT, "out", "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines).strip())

if __name__ == "__main__":
    main()
