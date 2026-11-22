"""Measure a revision with one context packet against saved full-workflow revisions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from run import HERE, PILOT_CASES, event_records, file_hash, phase, prepare, publish_report, write_json


MODEL = "gpt-6.2-sol"
REPLAY_TOOLS = {"pf", "pf_batch", "save_director_answer", "pf_publish"}
TASK = """Продолжи учебный проект для директора по продукту. Пакет ниже уже содержит статус,
решение, прежний отчётный текст, проверенные основания, новую выгрузку и правила сверки.
Работай с этим пакетом. Обращайся за дополнительными данными только если их здесь нет.
Используй только локальные MCP-инструменты, без внешнего поиска.

Зарегистрируй исправленное наблюдение через claim add с --src, точной --quote и --q.
Затем одним pf_batch обнови продление и его основание через decision pilot --id D001
--control-renewal <процент> --basis <новый C> и отзови прежние записи, которые источник
явно заменил. Цены, затраты, горизонт и порог остаются прежними. Если модельный процент
получен из чисел когорты, укажи это как расчёт. Финансовый блок добавит программа.

Сохрани через save_director_answer актуальный итог до 750 слов. В разделе «Ответ» укажи
рекомендацию, прежний порог и почему решение меняется, с действующей ссылкой [C...] и [D001].
Назови прежнее числовое наблюдение как отозванное и укажи новое со ссылкой. Сохрани
действующую цену, ограничения предложений, ограничения опроса и условия следующего шага.
Если решение учитывает сценарии продления, укажи для каждого, достигает ли он паритета.
Если в материалах есть модельное интервью, сохрани его статус и границы применимости.
В «Сверке оснований рекомендации» нужны колонки «Вывод», «Основание», «Что может
опровергнуть», «Когда меняем решение». В каждой строке должен быть действующий [C...]
или открытый [G...]. Отметь прежний вывод как исторический. Запуск утверждает директор.
Финансовые суммы бери из сохранённого расчёта, оставляя их в автоматическом блоке.
Выполни отдельные проверки по четырём ролям из пакета, запиши их одним pf_publish.
"""

STRUCTURED_TASK = """После save_director_answer вызови pf_foundations. Передай rows_json как JSON-массив.
Поля каждой строки: conclusion, refs (массив действующих ID C или открытых G), counter и revisit.
Таблица появляется в сохранённом итоге. После успешной сборки выполни pf_publish.
Пакет может содержать выдержки. Если не хватает прежнего значения, оговорки или условий, прочитай
источник через pf с args ["source", "read", "--id", "S001"] и нужным ID. Команда чтения решения —
args ["decision", "show", "--id", "D001"]. Для counter используй проверку из пакета.
Сохрани ограничения исходных данных.
"""


def replay(work, source, env):
    items = [e["item"] for e in event_records(source / "initial.events.jsonl")
             if e["type"] == "item.completed" and e["item"].get("tool") in REPLAY_TOOLS]
    requests = [dict(jsonrpc="2.0", id=i, method="tools/call",
                     params={"name": item["tool"], "arguments": item["arguments"]})
                for i, item in enumerate(items)]
    command = [sys.executable, str(work / "_bench_server.py"), str(work), "pf", "initial"]
    response = subprocess.run(command, cwd=work, env=env, input="\n".join(map(json.dumps, requests)),
                              text=True, encoding="utf-8", capture_output=True, check=True, timeout=90)
    replies = [json.loads(line)["result"] for line in response.stdout.splitlines()]
    if len(replies) != len(items) or any(r["isError"] != (i["status"] == "failed")
                                       for r, i in zip(replies, items)):
        raise ValueError(f"{source.name}: initial actions no longer replay with the recorded outcomes")


def normalized_report(path):
    return re.sub(r"(?m)^_Сформировано .+_\r?\n", "", path.read_text(encoding="utf-8")).strip()


def benchmark_case(codex, root, previous, case, timeout, prepare_only, structured):
    archived = previous / f"{case}.{MODEL}.pf"
    historical = root / f"{case}.{MODEL}.current"
    historical.mkdir()
    for artifact in archived.glob("revision.*"):
        shutil.copyfile(artifact, historical / artifact.name)
    control = json.loads((archived / "run.json").read_text(encoding="utf-8"))
    if control["revision"]["outcome"] != "published":
        raise ValueError(f"{case}: the archived full revision must be published")
    control.update(arm="current", initial=None)
    out = root / f"{case}.{MODEL}.compact"
    out.mkdir()
    with tempfile.TemporaryDirectory(prefix="pf-compact-revision-") as tmp:
        work = Path(tmp)
        prepare(work, case, "pf")
        env = {**os.environ, "PF_PROJECTS_DIR": str(work / "projects"), "PF_AGENT_ENV": "codex",
               "PYTHONIOENCODING": "utf-8"}
        env.pop("PF_PROJECT", None)
        replay(work, archived, env)
        seed, _, _ = publish_report(work, out, env, case, "initial")
        if seed != "published" or normalized_report(out / "initial.md") != normalized_report(archived / "initial.md"):
            raise ValueError(f"{case}: the replayed user-visible initial report differs")
        print(f"{case}: initial report replay verified", flush=True)
        started = time.monotonic()
        shutil.copyfile(HERE / "cases" / case / "update.md", work / "inbox" / "update.md")
        pf = [sys.executable, str(work / "tools/pf.py"), "--project", "experiment"]
        ledger = work / "projects/experiment/sources/registry.jsonl"
        before = {row["id"] for row in event_records(ledger)}
        subprocess.run(pf + ["ingest", "inbox/update.md"], cwd=work, env=env, check=True, capture_output=True)
        new_ids = {row["id"] for row in event_records(ledger)} - before
        if len(new_ids) != 1:
            raise ValueError(f"{case}: expected one new source")
        packet_args = pf + ["revision-packet", "--id", "D001", "--source", new_ids.pop(), "--review-rules"]
        packet = subprocess.run(
            packet_args + (["--compact"] if structured else []),
            cwd=work, env=env, encoding="utf-8", capture_output=True, check=True,
        ).stdout
        (out / "packet.md").write_text(packet, encoding="utf-8")
        prep = round(time.monotonic() - started, 2)
        if structured and prepare_only:
            full = subprocess.run(packet_args, cwd=work, env=env, encoding="utf-8", capture_output=True,
                                  check=True).stdout
            write_json(out / "packet.metrics.json", {"full_chars": len(full), "compact_chars": len(packet)})
        if prepare_only:
            return []
        revision = phase(codex, work, out, env, MODEL, "pf", case, "revision", "", timeout,
                         prompt_override=TASK + (STRUCTURED_TASK if structured else "") + "\n\n" + packet)
        revision["preparation_seconds"] = prep
        revision["seconds"] = round(revision["seconds"] + prep, 2)
        write_json(out / "revision.metrics.json", revision)
        shutil.copytree(work / "projects", out / "projects")
    return [control, dict(case=case, model=MODEL, arm="compact", initial=None, revision=revision,
                          release_code=revision["release_code"])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("--case", choices=PILOT_CASES, action="append")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--structured", action="store_true", help="use bounded packets and generate the table")
    opts = parser.parse_args()
    out, previous = opts.out.resolve(), opts.previous.resolve()
    info = json.loads((previous / "experiment.json").read_text(encoding="utf-8"))
    cases = opts.case or PILOT_CASES
    inputs = {case: {name: file_hash(HERE / "cases" / case / name) for name in ("initial.md", "update.md")}
              for case in cases}
    if info.get("effort") != "low" or any(info["input_sha256"].get(case) != data for case, data in inputs.items()):
        parser.error("the full-workflow comparison requires the same inputs and effort")
    codex = shutil.which("codex")
    if not opts.prepare_only and not codex:
        parser.error("Codex CLI must be on PATH")
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "experiment.json", dict(evaluation_artifact="released_report",
               input_sha256=inputs, model=MODEL, effort="low", baseline=str(previous),
               phase="revision", structured=opts.structured))
    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        jobs = [pool.submit(benchmark_case, codex, out, previous, case, opts.timeout,
                            opts.prepare_only, opts.structured)
                for case in cases]
        results = [row for job in jobs for row in job.result()]
    if not opts.prepare_only:
        write_json(out / "results.json", results)


if __name__ == "__main__":
    main()
