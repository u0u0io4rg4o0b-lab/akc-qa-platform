#!/usr/bin/env python3
"""AKC QA personal preview: planned trusted local commands and portable receipts.

No third-party dependencies. This is not an isolation or security boundary.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

VERSION = "0.4.0-preview.1"
BASE = Path(__file__).resolve().parent
MAX_FILE = 4 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
CEILING = "TRUSTED_LOCAL_CHECKS_ONLY"


class QAError(Exception):
    pass


def need(condition, message):
    if not condition:
        raise QAError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, "JSON 重複欄位：" + key)
        result[key] = value
    return result


def parse(data):
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(QAError("JSON 非有限數值")))


def regular(path, limit=MAX_FILE):
    path = Path(path)
    need(not path.is_symlink() and path.is_file(), "需要一般檔案：" + str(path))
    need(path.stat().st_nlink == 1, "不接受硬連結：" + str(path))
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    need(len(data) <= limit, "檔案超過上限：" + str(path))
    return data


def load(path):
    return parse(regular(path))


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(canonical(value) + b"\n")


def member(root, name):
    need(isinstance(name, str) and name and "\\" not in name and ":" not in name,
         "需要包內相對路徑")
    rel = PurePosixPath(name)
    need(not rel.is_absolute() and all(p not in (".", "..", "") for p in name.split("/")),
         "路徑不能越界")
    root = Path(root).resolve()
    path = root
    for part in rel.parts:
        path = path / part
        need(not path.is_symlink(), "不接受符號連結：" + name)
    need(root in path.resolve().parents, "路徑離開根目錄")
    return path


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate(task):
    need(isinstance(task, dict) and type(task.get("schema_version")) is int
         and task["schema_version"] == 1, "task schema_version 需為 1")
    for field in ("name", "version"):
        need(nonempty(task.get("project", {}).get(field)), "缺 project." + field)
    scope = task.get("scope", {})
    need(isinstance(scope.get("included"), list) and scope["included"]
         and all(nonempty(v) for v in scope["included"]), "請列出 scope.included")
    need(isinstance(scope.get("excluded"), list) and all(nonempty(v) for v in scope["excluded"]),
         "請列出 scope.excluded，沒有則空陣列")
    files = task.get("files")
    need(isinstance(files, list) and 0 < len(files) <= 256
         and all(nonempty(f) for f in files) and len(files) == len(set(files)),
         "files 需為 1–256 個不重複來源／測具／需求／鎖檔路徑")
    requirements = task.get("requirements")
    need(isinstance(requirements, list) and requirements, "缺 requirements")
    ids = []
    for req in requirements:
        need(isinstance(req, dict) and isinstance(req.get("id"), str)
             and IDENTIFIER.fullmatch(req["id"]), "需求 ID 不合法")
        need(nonempty(req.get("text")) and nonempty(req.get("source")), "需求需有內容與正解來源")
        ids.append(req["id"])
    need(len(ids) == len(set(ids)), "需求 ID 重複")
    checks = task.get("checks")
    need(isinstance(checks, list) and 0 < len(checks) <= 64, "需有 1–64 項 checks")
    check_ids = []
    for check in checks:
        need(isinstance(check, dict) and isinstance(check.get("id"), str)
             and IDENTIFIER.fullmatch(check["id"]), "check ID 不合法")
        check_ids.append(check["id"])
        need(check.get("requirement_id") in ids, "check 未指向已列需求")
        need(nonempty(check.get("description")), "check 缺 description")
        argv = check.get("argv")
        need(isinstance(argv, list) and argv and all(isinstance(v, str) and "\x00" not in v for v in argv),
             "argv 需為字串陣列，不接受 shell 字串")
        need(type(check.get("expected_exit")) is int, "expected_exit 需為整數")
        need(type(check.get("timeout_seconds")) in (int, float)
             and 0 < check["timeout_seconds"] <= 120, "timeout_seconds 需為 0–120 秒")
        need(check.get("mode") in ("json_equals", "exit_code"), "mode 需為 json_equals 或 exit_code")
        if check["mode"] == "json_equals":
            need("expected_json" in check, "json_equals 缺 expected_json")
        need(check.get("expected_stderr", "") == "", "本版只接受空 stderr；診斷輸出仍會保存")
    need(len(check_ids) == len(set(check_ids)), "check ID 重複")
    canonical(task)


def environment():
    return {"python": platform.python_version(), "system": platform.system(),
            "machine": platform.machine(), "python_executable": str(Path(sys.executable).resolve()),
            "python_sha256": sha(Path(sys.executable).resolve().read_bytes())}


def identities(task, root):
    return [{"path": name, "sha256": sha(regular(member(root, name)))} for name in task["files"]]


def resolve_argv(check, root):
    argv = [arg.replace("{python}", sys.executable).replace("{project}", str(root)) for arg in check["argv"]]
    executable = shutil.which(argv[0])
    need(executable is not None, "找不到執行器：" + argv[0])
    argv[0] = str(Path(executable).resolve())
    # Windows batch launchers can invoke a shell even with shell=False.
    need(Path(argv[0]).suffix.lower() not in (".bat", ".cmd"),
         "本版不執行 Windows bat/cmd；請使用真正執行檔或 Python／Node 入口")
    return {"id": check["id"], "argv": argv,
            "executable_sha256": sha(Path(argv[0]).read_bytes())}


def make_plan(task_path, project_root):
    task = load(task_path)
    validate(task)
    root = Path(project_root).resolve(strict=True)
    need(root.is_dir(), "project-root 需為資料夾")
    body = {"schema_version": 1, "tool_version": VERSION, "tool_sha256": sha(regular(__file__)),
            "task": task, "project_root": str(root), "files": identities(task, root),
            "commands": [resolve_argv(c, root) for c in task["checks"]], "environment": environment(),
            "execution_profile": "trusted-local-direct-command",
            "isolation": "NONE", "network_enforcement": "NONE",
            "dependency_coverage": "EXPLICIT_FILES_ONLY", "claim_ceiling": CEILING}
    return {"plan_id": sha(canonical(body)), "plan": body}


def check_plan(value, live=False):
    need(isinstance(value, dict) and sha(canonical(value["plan"])) == value.get("plan_id"), "計畫身分不符")
    body = value["plan"]
    validate(body["task"])
    need(body["tool_version"] == VERSION and body["claim_ceiling"] == CEILING,
         "計畫版本或結論範圍不符")
    need(body["execution_profile"] == "trusted-local-direct-command"
         and body["isolation"] == "NONE" and body["network_enforcement"] == "NONE", "執行 profile 不符")
    need([v["path"] for v in body["files"]] == body["task"]["files"], "來源集合不符")
    need([v["id"] for v in body["commands"]] == [c["id"] for c in body["task"]["checks"]], "命令集合不符")
    if live:
        need(body["tool_sha256"] == sha(regular(__file__)), "工具變更，請建立新計畫")
        need(body["environment"] == environment(), "執行環境變更，請建立新計畫")
        root = Path(body["project_root"])
        need(body["files"] == identities(body["task"], root), "來源／測具／鎖檔已變更，請建立新計畫")
        need(body["commands"] == [resolve_argv(c, root) for c in body["task"]["checks"]], "執行器已變更")


def capture(argv, cwd, timeout):
    output = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    truncated = {"stdout": False, "stderr": False}
    capture_error = threading.Event()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tick = time.monotonic()
    kwargs = {"start_new_session": True} if os.name == "posix" else {}
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, shell=False, **kwargs)

    def read_stream(name, pipe):
        try:
            while True:
                block = os.read(pipe.fileno(), 65536)
                if not block:
                    break
                remaining = MAX_OUTPUT - len(output[name])
                output[name].extend(block[:remaining])
                if len(block) > remaining:
                    truncated[name] = True
                    overflow.set()
        except (OSError, ValueError):
            capture_error.set()

    threads = [threading.Thread(target=read_stream, args=(name, pipe), daemon=True)
               for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
    for thread in threads:
        thread.start()
    state = "COMPLETED"
    interrupted = False
    try:
        while process.poll() is None or any(t.is_alive() for t in threads):
            if overflow.is_set():
                state = "OUTPUT_LIMIT"
                break
            if time.monotonic() - tick > timeout:
                state = "TIMED_OUT"
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        state, interrupted = "CANCELLED", True
    finally:
        # Best effort only: daemonized/detached descendants are not contained.
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            state = "CLEANUP_UNKNOWN"
        for thread in threads:
            thread.join(timeout=0.5)
        readers_done = not any(t.is_alive() for t in threads)
        if readers_done:
            process.stdout.close()
            process.stderr.close()
        if overflow.is_set() and state == "COMPLETED":
            state = "OUTPUT_LIMIT"
        if capture_error.is_set():
            state = "CAPTURE_ERROR"
        if not readers_done:
            state = "CLEANUP_UNKNOWN"
    record = {"argv": argv, "started_at_utc": started,
              "ended_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "duration_seconds": round(time.monotonic() - tick, 6), "pid": process.pid,
              "returncode": process.returncode, "state": state,
              "stdout_truncated": truncated["stdout"], "stderr_truncated": truncated["stderr"],
              "cleanup": "BEST_EFFORT_PROCESS_GROUP" if os.name == "posix" else "PARENT_ONLY",
              "detached_descendants": "UNKNOWN", "effects_rollback": "NOT_PROVIDED"}
    return record, {k: bytes(v) for k, v in output.items()}, interrupted


def judge(check, native, streams):
    if native["state"] == "NOT_RUN":
        return "NOT_RUN"
    if native["state"] != "COMPLETED" or native["stdout_truncated"] or native["stderr_truncated"]:
        return "INCONCLUSIVE"
    if native["returncode"] != check["expected_exit"] or streams["stderr"]:
        return "FAIL"
    if check["mode"] == "exit_code":
        return "EXIT_MATCH"
    try:
        # Canonical equality keeps true distinct from 1, and rejects trailing output.
        return "PASS" if canonical(parse(streams["stdout"].decode("utf-8"))) == canonical(check["expected_json"]) else "FAIL"
    except (ValueError, UnicodeError, QAError):
        return "INCONCLUSIVE"


def summary(plan, records):
    checks = plan["plan"]["task"]["checks"]
    need(len(records) == len(checks), "案例分母不符")
    statuses = [r["verdict"] for r in records]
    counts = {state: statuses.count(state) for state in ("PASS", "FAIL", "INCONCLUSIVE", "NOT_RUN", "EXIT_MATCH")}
    if counts["FAIL"]:
        verdict = "FAIL"
    elif counts["INCONCLUSIVE"] or counts["NOT_RUN"]:
        verdict = "INCONCLUSIVE"
    elif counts["EXIT_MATCH"]:
        verdict = "COMMANDS_COMPLETED_QA_UNCONFIRMED"
    else:
        verdict = "PASS"
    return {"schema_version": 1, "plan_id": plan["plan_id"], "checks": records,
            "total": len(checks), "counts": counts, "verdict": verdict, "claim_ceiling": CEILING,
            "independent_review": False, "security_audit": False, "product_acceptance": False,
            "uncovered_requirement_ids": [r["id"] for r in plan["plan"]["task"]["requirements"]
                                          if r["id"] not in {c["requirement_id"] for c in checks}]}


def report(value):
    lines = ["# AKC QA 本機結果", "", "結果：**" + value["verdict"] + "**；計畫 `" + value["plan_id"] + "`。",
             "", "已列檢查 %s 項：%s。" % (value["total"], json.dumps(value["counts"], ensure_ascii=False)),
             "", "| 檢查 | 判讀 | 原始證據 |", "|---|---|---|"]
    lines += ["| %s | %s | [收據](checks/%s/native.json) |" % (r["id"], r["verdict"], r["id"]) for r in value["checks"]]
    lines += ["", "未有檢查的需求：" + (", ".join(value["uncovered_requirement_ids"]) or "無；僅限計畫所列需求"),
              "", "PASS 只表示本計畫指定的命令輸出符合判準；EXIT_MATCH 只表示退出碼符合。",
              "這些是同一使用者可修改的本機證據，不是獨立見證、安全審計或完整產品驗收。",
              "命令以目前使用者權限執行，沒有網路／檔案隔離；清理為盡力處理，沒有回滾保證。",
              "需求、排除範圍、明列來源與執行環境見 [plan.json](plan.json)。原始 stdout.bin／stderr.bin 可能含私人資料，分享前另行審閱。"]
    return "\n".join(lines) + "\n"


def seal(root):
    entries = []
    for path in sorted(root.rglob("*")):
        need(not path.is_symlink(), "證據含符號連結")
        if path.is_file():
            entries.append({"path": path.relative_to(root).as_posix(), "sha256": sha(regular(path))})
    put(root / "manifest.json", {"schema_version": 1, "files": entries})


def run_plan(plan_path, out, approval):
    plan = load(plan_path)
    check_plan(plan, live=True)
    need(approval == plan["plan_id"], "需 --approve 計畫完整 plan_id；請先核對命令與可信來源")
    out = Path(out).absolute()
    need(not out.exists(), "結果目錄已存在；請讀回舊結果或指定新的 attempt 目錄")
    # mkdir(exist_ok=False) also prevents concurrent launches into this attempt.
    out.mkdir(parents=True)
    put(out / "plan.json", plan)
    put(out / "attempt.json", {"attempt_id": str(uuid.uuid4()), "plan_id": plan["plan_id"],
                               "status": "STARTED", "automatic_replay": False})
    records, stop = [], False
    for check, command in zip(plan["plan"]["task"]["checks"], plan["plan"]["commands"]):
        directory = out / "checks" / check["id"]
        directory.mkdir(parents=True)
        streams = {"stdout": b"", "stderr": b""}
        native = {"case_id": check["id"], "plan_id": plan["plan_id"], "argv": command["argv"],
                  "returncode": None, "state": "NOT_RUN", "stdout_truncated": False, "stderr_truncated": False}
        if not stop:
            try:
                check_plan(plan, live=True)
                captured, streams, interrupted = capture(command["argv"], plan["plan"]["project_root"], check["timeout_seconds"])
                native.update(captured)
                # Source mutation during a trusted command invalidates this observation.
                check_plan(plan, live=True)
                stop = interrupted
            except (OSError, QAError) as error:
                native.update(state="INPUT_OR_EXECUTION_CHANGED", diagnostic=str(error))
                stop = True
        verdict = judge(check, native, streams)
        native["verdict"] = verdict
        for name, data in streams.items():
            with (directory / (name + ".bin")).open("xb") as handle:
                handle.write(data)
        put(directory / "native.json", native)
        records.append({"id": check["id"], "verdict": verdict})
        stop = stop or verdict not in ("PASS", "EXIT_MATCH")
    value = summary(plan, records)
    put(out / "result.json", value)
    with (out / "REPORT.md").open("x", encoding="utf-8") as handle:
        handle.write(report(value))
    seal(out)
    return value


def verify(root):
    root = Path(root).resolve()
    manifest = load(root / "manifest.json")
    entries = manifest["files"]
    names = [e["path"] for e in entries]
    need(len(names) == len(set(names)) and "manifest.json" not in names, "manifest 路徑重複／不合法")
    actual = set()
    for path in root.rglob("*"):
        need(not path.is_symlink(), "證據含符號連結")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    need(actual == set(names) | {"manifest.json"}, "證據缺件或多出未列檔案")
    for entry in entries:
        need(sha(regular(member(root, entry["path"]))) == entry["sha256"], "證據內容已變更：" + entry["path"])
    plan = load(root / "plan.json")
    check_plan(plan)
    attempt = load(root / "attempt.json")
    need(attempt["plan_id"] == plan["plan_id"] and nonempty(attempt["attempt_id"]), "attempt 身分不符")
    records = []
    for check, command in zip(plan["plan"]["task"]["checks"], plan["plan"]["commands"]):
        directory = member(root, "checks/" + check["id"])
        native = load(directory / "native.json")
        need(native["case_id"] == check["id"] and native["plan_id"] == plan["plan_id"]
             and native["argv"] == command["argv"], "原生收據與計畫不符")
        streams = {name: regular(directory / (name + ".bin")) for name in ("stdout", "stderr")}
        verdict = judge(check, native, streams)
        need(verdict == native["verdict"], "收據判讀與原始輸出不符")
        records.append({"id": check["id"], "verdict": verdict})
    expected = summary(plan, records)
    need(canonical(load(root / "result.json")) == canonical(expected), "摘要與重算結果不符")
    need(regular(root / "REPORT.md") == report(expected).encode("utf-8"), "報告與重算結果不符")
    return {"integrity": "MATCH", "judgment_recheck": "MATCH", "verdict": expected["verdict"],
            "total": expected["total"], "counts": expected["counts"],
            "source_trust": "USER_CONTROLLED_NOT_INDEPENDENTLY_ATTESTED", "claim_ceiling": CEILING}


def doctor():
    return {"tool": VERSION, "python": platform.python_version(),
            "runtime_supported": sys.version_info >= (3, 11),
            "third_party_dependencies": [], "isolation": "NONE", "network_enforcement": "NONE",
            "next": "python qa.py demo，或依 START_HERE.md 規劃自己的可信本機專案"}


def main():
    parser = argparse.ArgumentParser(description="AKC QA：個人非商業、本機可信專案 QA 預覽版")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    demo = sub.add_parser("demo")
    demo.add_argument("--out")
    plan = sub.add_parser("plan")
    plan.add_argument("task")
    plan.add_argument("--project-root", required=True)
    plan.add_argument("--out", required=True)
    run = sub.add_parser("run")
    run.add_argument("plan")
    run.add_argument("--out", required=True)
    run.add_argument("--approve", required=True)
    check = sub.add_parser("verify")
    check.add_argument("bundle")
    args = parser.parse_args()
    try:
        need(sys.version_info >= (3, 11), "請使用 Python 3.11 以上；macOS 可檢查 python3，Windows 可檢查 py -3")
        if args.command == "doctor":
            answer = doctor()
        elif args.command == "plan":
            value = make_plan(args.task, args.project_root)
            put(args.out, value)
            answer = {"status": "PLANNED_NOT_EXECUTED", "plan_id": value["plan_id"], "plan": str(args.out)}
        elif args.command == "run":
            answer = run_plan(args.plan, args.out, args.approve)
        elif args.command == "verify":
            answer = verify(args.bundle)
        else:
            destination = Path(args.out) if args.out else BASE / "runs" / ("demo-" + uuid.uuid4().hex[:12])
            need(not destination.exists(), "demo 目錄已存在，請改用新目錄")
            destination.mkdir(parents=True)
            value = make_plan(BASE / "examples" / "discount" / "qa-task.json", BASE / "examples" / "discount")
            put(destination / "approved-demo-plan.json", value)
            answer = run_plan(destination / "approved-demo-plan.json", destination / "evidence", value["plan_id"])
            verify(destination / "evidence")
            answer["report"] = str((destination / "evidence" / "REPORT.md").resolve())
        print(json.dumps(answer, ensure_ascii=False, indent=2))
        if args.command in ("run", "demo", "verify"):
            return 0 if answer["verdict"] in ("PASS", "COMMANDS_COMPLETED_QA_UNCONFIRMED") else 1
        return 0
    except (QAError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({"status": "BLOCKED", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
