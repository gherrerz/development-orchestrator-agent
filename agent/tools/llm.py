import os
import json
import re
from typing import Any, Dict, Optional
from datetime import datetime
from pathlib import Path

from openai import OpenAI

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

_client: Optional[OpenAI] = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _out_dir() -> Path:
    p = Path("agent/out")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_raw(name: str, content: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    path = _out_dir() / f"{name}_{ts}.txt"
    path.write_text(content or "", encoding="utf-8", errors="replace")
    return str(path)


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    # Remove triple backticks blocks if present
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _extract_json_object(s: str) -> str:
    """
    Best-effort extraction of the first JSON object in a text blob.
    This helps when the model returns extra text or code fences.
    """
    s = _strip_fences(s)
    # Fast path
    if s.startswith("{") and s.endswith("}"):
        return s

    # Find outermost object heuristically: first '{' and last '}'
    i = s.find("{")
    j = s.rfind("}")
    if i != -1 and j != -1 and j > i:
        return s[i : j + 1].strip()

    return s.strip()


def _repair_json_with_model(
    *,
    schema_name: str,
    raw_text: str,
    temperature: float,
) -> Dict[str, Any]:
    """
    One-shot JSON repair via the model. Keep it short to avoid truncation loops.
    """
    system = (
        "You are a JSON repair assistant.\n"
        f"Return ONLY a valid JSON object for schema: {schema_name}.\n"
        "No markdown. No explanations.\n"
        "If the input is too large or truncated, output a smaller but valid JSON that preserves intent.\n"
        "Do NOT include trailing commas. Ensure all strings are closed.\n"
    )

    user = json.dumps(
        {
            "schema_name": schema_name,
            "broken_output": raw_text[:200000],  # hard cap to avoid huge re-prompt
        },
        ensure_ascii=False,
    )

    resp = client().chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content or ""
    # parse repaired
    extracted = _extract_json_object(content)
    return json.loads(extracted)


def chat_json(system: str, user: str, schema_name: str, temperature: float = 0.2) -> Dict[str, Any]:
    """
    Enterprise-hardened JSON chat:
    - Saves raw model output to agent/out on failure
    - Best-effort JSON extraction (strip fences, take {...})
    - One repair attempt via model if parsing fails
    """
    messages = [
        {
            "role": "system",
            "content": system
            + "\n\nOutput MUST be valid JSON for schema: "
            + schema_name
            + ". No markdown. No extra text.",
        },
        {"role": "user", "content": user},
    ]

    resp = client().chat.completions.create(
        model=DEFAULT_MODEL,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or ""

    try:
        extracted = _extract_json_object(content)
        return json.loads(extracted)
    except Exception as e:
        raw_path = _write_raw(f"llm_raw_{schema_name}", content)
        # One repair attempt
        try:
            repaired = _repair_json_with_model(
                schema_name=schema_name,
                raw_text=content,
                temperature=0.0,
            )
            return repaired
        except Exception as e2:
            # Save repair failure details
            _write_raw(f"llm_parse_error_{schema_name}", f"{type(e).__name__}: {e}\nRAW={raw_path}\n")
            raise ValueError(
                f"LLM output no fue JSON válido para {schema_name}. "
                f"Se guardó raw en {raw_path}. "
                f"Repair falló: {type(e2).__name__}: {e2}"
            ) from e2
