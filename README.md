# development-orchestrator-agent
# GitHub Actions Agent Orchestrator (Python)

## Trigger
Comenta en un Issue:

/agent run { ...json... }

Ejemplo:

/agent run {
  "stack": "python-fastapi",
  "language": "python",
  "user_story": "Como usuario, quiero ...",
  "acceptance_criteria": ["..."],
  "constraints": ["..."],
  "test_command": "pytest -q",
  "max_iterations": 2
}

## Secrets requeridos
- OPENAI_API_KEY
- PINECONE_API_KEY
- PINECONE_INDEX_HOST
Opcionales:
- OPENAI_MODEL
- OPENAI_EMBED_MODEL
- PINECONE_NAMESPACE_PREFIX

## Resultado
El agente:
1) genera un plan,
2) aplica cambios,
3) corre tests,
4) crea PR con evidencia y comenta al Issue.
