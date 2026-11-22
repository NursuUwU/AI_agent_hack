"""Scope tools to one paired benchmark workspace and its staged materials."""

import ast
from decimal import Decimal, DecimalException
import json
import operator
from pathlib import Path
import shlex
import subprocess
import sys


SLUG = "experiment"
SUMMARY = "deliverables/summary.md"
PF_COMMANDS = {"status", "set", "question", "claim", "gap", "decision", "digest", "focus",
               "check", "impact", "log", "verdict", "stage", "report", "source"}


def tool(tool_name, description, **fields):
    return {"name": tool_name, "description": description,
            "annotations": {"readOnlyHint": tool_name in ("read_case_material", "calculate", "pilot_economics", "guide"),
                            "destructiveHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": fields, "required": list(fields),
                            "additionalProperties": False}}


STRING = {"type": "string"}
ARGS = {"type": "array", "items": STRING}
COMMON_TOOLS = [
    tool("read_case_material", "Материалы кейса: initial, update (только во второй фазе), previous_answer.", name=STRING),
    tool("save_director_answer", "Сохранить итоговый Markdown для директора и для следующего чата.", markdown=STRING),
    tool("calculate", "Вычислить числовое выражение: +, -, *, /, ** и sqrt(...).", expression=STRING),
]
PF_TOOLS = [
    tool("pf", "CLI агента. args — список аргументов. init experiment; ingest inbox/initial.md или "
         "inbox/update.md; status --brief; question, claim, gap, decision, source read/list, focus, digest, "
         "impact, verdict, check. save_director_answer также пишет deliverables/summary.md.", args=ARGS),
    tool("pf_batch", "Несколько команд pf. Каждая команда — массив аргументов.",
         commands={"type": "array", "items": ARGS}),
    tool("pf_foundations", "После сохранения итога собери таблицу оснований, передав строки с полями "
         "conclusion, refs (массив ID), counter и revisit. rows_json — JSON-массив.",
         rows_json=STRING),
    tool("pilot_economics", "Экономика ценового пилота подписки из tools/pilot_economics.py. "
         "Вызови с [\"--help\"] для аргументов.", args=ARGS),
    tool("guide", "Читать Markdown с методикой, шаблоном, skill или ролью. Путь от корня: "
         "knowledge/, .claude/skills/ или .claude/agents/.", path=STRING),
    tool("pf_publish", "После сохранения итога запиши результаты отдельных проходов и выполни "
         "проверку выпуска. Укажи конкретные выводы по каждой роли.",
         red_team=STRING, judge=STRING, counter=STRING, fidelity=STRING),
]


def calculate(expression):
    if len(expression) > 240:
        raise ValueError("Слишком длинное выражение.")
    powers = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = value(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if type(node.op) in powers:
                return powers[type(node.op)](left, right)
            if isinstance(node.op, ast.Pow) and right == int(right) and abs(right) <= 120:
                return left ** int(right)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt"
                and len(node.args) == 1 and not node.keywords):
            return value(node.args[0]).sqrt()
        raise ValueError("Только числовые операции и sqrt.")

    return str(value(ast.parse(expression.strip(), mode="eval").body))


def validate_command(args, phase):
    if not args or len(args) > 45 or any(not isinstance(arg, str) for arg in args):
        raise ValueError("Передай список аргументов одной команды pf.")
    for index, arg in enumerate(args):
        flag, separator, value = arg.partition("=")
        if flag in ("--project", "-p", "--draft", "--out", "--from") or (
                flag == "--executor" and (value if separator else (args[index + 1:index + 2] or [""])[0]) != "self"):
            raise ValueError("Этот параметр управляется стендом. Для --executor доступно self.")
    command, *tail = args
    if command == "init" and tail and tail[0] == SLUG:
        return
    if command == "ingest" and len(tail) == 1:
        if tail[0] == "inbox/initial.md" or (tail[0] == "inbox/update.md" and phase == "revision"):
            return
    if command in PF_COMMANDS and (command != "source" or tail and tail[0] in ("read", "list", "--help")):
        if command != "verdict" or SUMMARY in tail or tail in (["list"], ["--help"]):
            return
    raise ValueError("Команда выходит за пределы стенда. Для verdict используй deliverables/summary.md.")


def execute(work, script, args, stdin=None):
    command = [sys.executable, "-X", "utf8", str(work / "tools" / script), *args]
    run = subprocess.run(command, cwd=work, input=stdin, encoding="utf-8",
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
    return run.stdout.strip(), run.returncode != 0


def call(work, arm, phase, name, args):
    if name == "read_case_material":
        paths = {"initial": "inbox/initial.md"}
        if phase == "revision":
            paths.update({"update": "inbox/update.md", "previous_answer": "initial.md"})
        path = paths.get(args["name"])
        if not path:
            return "В этой фазе материал недоступен.", True
        return (work / path).read_text(encoding="utf-8"), False
    if name == "save_director_answer":
        answer = args["markdown"]
        if not answer.strip() or len(answer) > 16_000:
            return "Нужен непустой Markdown до 16 000 знаков.", True
        (work / f"{phase}.md").write_text(answer, encoding="utf-8")
        summary = work / "projects" / SLUG / SUMMARY
        if arm == "pf" and summary.parent.is_dir():
            summary.write_text(answer, encoding="utf-8")
        return "Сохранено.", False
    if name == "calculate":
        return calculate(args["expression"]), False
    if arm != "pf":
        return "Инструмент недоступен.", True
    if name == "guide":
        path = (work / args["path"]).resolve()
        roots = [work / prefix for prefix in ("knowledge", ".claude/skills", ".claude/agents")]
        if path.suffix != ".md" or not any(path.is_relative_to(root.resolve()) for root in roots):
            return "Доступны только Markdown-файлы методик, шаблонов, skills и ролей.", True
        return path.read_text(encoding="utf-8"), False
    if name == "pilot_economics":
        return execute(work, "pilot_economics.py", args["args"])
    if name == "pf_publish":
        command = ["verdict", "add", "--target", SUMMARY, "--executor", "self", "--check-release"]
        for role in ("red_team", "judge", "counter", "fidelity"):
            command.extend(["--review", role.replace("_", "-"), args[role]])
        return execute(work, "pf.py", command)
    if name == "pf_foundations":
        return execute(work, "pf.py", ["foundations", "--target", SUMMARY], args["rows_json"])
    if name == "pf":
        validate_command(args["args"], phase)
        return execute(work, "pf.py", args["args"])
    if name == "pf_batch":
        commands = args["commands"]
        if not 1 <= len(commands) <= 30:
            raise ValueError("Допустимо от 1 до 30 команд за пакет.")
        lines = []
        for command in commands:
            validate_command(command, phase)
            # The citation checker already compares whitespace-normalized excerpts.
            quoted = [" ".join(arg.split()) if i and command[i - 1] == "--quote" else arg
                      for i, arg in enumerate(command)]
            if any("\r" in arg or "\n" in arg for arg in quoted):
                raise ValueError("Для многострочных аргументов используй одиночную команду pf.")
            lines.append(shlex.join(quoted))
        return execute(work, "pf.py", ["batch", "-", "--quiet"], "\n".join(lines))
    return "Неизвестный инструмент.", True


def serve(work, arm, phase):
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        method, params = message["method"], message.get("params", {})
        if method == "initialize":
            reply["result"] = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                               "serverInfo": {"name": "paired-benchmark", "version": "1"}}
        elif method == "tools/list":
            reply["result"] = {"tools": COMMON_TOOLS + (PF_TOOLS if arm == "pf" else [])}
        elif method == "tools/call":
            try:
                output, failed = call(work, arm, phase, params["name"], params.get("arguments", {}))
            except (ValueError, KeyError, DecimalException, SyntaxError, OSError, subprocess.SubprocessError) as exc:
                output, failed = str(exc), True
            reply["result"] = {"content": [{"type": "text", "text": output}], "isError": failed}
        else:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        print(json.dumps(reply, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
