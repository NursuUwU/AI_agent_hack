"""Turn one correction file into a checked preview with one Codex extraction call."""

from pathlib import Path
import re
import subprocess
import sys
import time

from structured_codex import run_json


FIELDS = {"status": {"type": "string", "enum": ["ready", "review"]},
          "terms": {"type": "string"}, "replacement": {"type": "string"},
          "observations": {"type": "string"}, "scenario": {"type": "string"},
          "cohort": {"type": "array", "items": {"type": "integer"}},
          "highlights": {"type": "array", "items": {"type": "string"}}}


def triage(pf, codex, workspace, env, decision_id, source_file, model, timeout=90):
    def project_command(args, *, input=None):
        return subprocess.run([sys.executable, str(pf), *args], cwd=workspace, env=env,
                              input=input, capture_output=True, encoding="utf-8", timeout=30)

    started = time.monotonic()
    ingest = project_command(["ingest", str(source_file)])
    if ingest.returncode:
        raise ValueError(ingest.stderr.strip() or ingest.stdout.strip())
    refs = re.findall(r"(?m)^(S\d+)\t", ingest.stdout)
    if len(refs) != 1:
        raise ValueError("требуется один зарегистрированный файл поправки")
    cmd = ["pilot-triage", "--id", decision_id, "--source", refs[0]]
    prompt = project_command(cmd)
    if prompt.returncode:
        raise ValueError(prompt.stderr.strip() or prompt.stdout.strip())

    schema = {"type": "object", "properties": FIELDS, "required": list(FIELDS), "additionalProperties": False}
    proposal, usage = run_json(codex, model, prompt.stdout, schema, env, timeout)
    result = project_command(cmd + ["--proposal", "-"], input=proposal)
    return (result.stdout, result.stderr, result.returncode,
            {"seconds": round(time.monotonic() - started, 2), "model": model, "model_calls": 1,
             "manual_transcriptions": 0, "source": refs[0], "usage": usage,
             "extraction": proposal})
