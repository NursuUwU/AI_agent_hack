"""Run one schema-constrained Codex turn on the supplied data."""

import json
from pathlib import Path
import subprocess
import tempfile


def run_json(codex, model, prompt, schema, env, timeout):
    with tempfile.TemporaryDirectory(prefix="pf-extract-") as local:
        work = Path(local)
        answer = work / "extraction.json"
        schema_file = work / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        args = [codex, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                "--sandbox", "read-only", "--json", "--model", model, "-c", 'model_reasoning_effort="low"',
                "-c", 'web_search="disabled"', "-c", 'approval_policy="never"', "-c", "project_doc_max_bytes=0",
                "--output-schema", str(schema_file), "--output-last-message", str(answer), "-"]
        run = subprocess.run(args, input=prompt, cwd=work, env=env, encoding="utf-8",
                             capture_output=True, timeout=timeout)
        events = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
        if run.returncode or not answer.is_file():
            error = next((event.get("message") for event in reversed(events) if event.get("type") == "error"), None)
            raise ValueError(error or run.stderr.strip() or "Codex не сохранил извлечение")
        proposal = answer.read_text(encoding="utf-8").strip()
        if proposal.startswith('```json\n') and proposal.endswith('```'):
            proposal = proposal[len('```json\n'):-3].strip()
    usage = next((event["usage"] for event in reversed(events) if event.get("type") == "turn.completed"), {})
    return proposal, usage
