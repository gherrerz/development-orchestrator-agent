import os
import json
from typing import Any, Dict, Optional
from openai import OpenAI

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

_client: Optional[OpenAI] = None

def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client

def chat_json(system: str, user: str, schema_name: str, temperature: float = 0.2) -> Dict[str, Any]:
    """
    Forces the model to output JSON only (best-effort). We still validate with jsonschema outside.
    """
    messages = [
        {"role": "system", "content": system + f"\n\nOutput MUST be valid JSON for schema: {schema_name}. No markdown."},
        {"role": "user", "content": user},
    ]
    resp = client().chat.completions.create(
        model=DEFAULT_MODEL,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"}
    )
    content = resp.choices[0].message.content
    return json.loads(content)
