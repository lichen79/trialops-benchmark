"""One agent loop, identical for every model: OpenAI-style tool calling over a throwaway repo.

Open-weight models: Bedrock's OpenAI-compatible Chat Completions surface, reached through the
local SigV4 signing proxy (http://127.0.0.1:8787/v1). Proprietary references (Claude): Bedrock
Converse, because Claude is not served on the OpenAI surface; `ConverseAdapter` translates the
same messages and tool schemas, so the loop, prompt, tools and limits are unchanged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

PROXY = "http://127.0.0.1:8787/v1/chat/completions"
REGION = "us-west-2"
MAX_CALLS = 30          # model calls per run
MAX_WALL_S = 20 * 60    # wall clock per run
MAX_TOKENS = 16384      # completion budget per call (reasoning included)
TEST_TIMEOUT_S = 90
PY = sys.executable

SYSTEM = (
    "You are an autonomous coding agent working in a Python repository (the current directory). "
    "Complete the developer's request by inspecting files, editing them and running the tests with "
    "the provided tools. Make minimal, correct changes; keep existing behaviour and tests passing; "
    "add tests when asked. When the work is complete and the tests pass, call `finish` with a short "
    "summary. Do not ask questions: there is no human in the loop."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files under a directory of the repository (recursive).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Directory, default '.'"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a UTF-8 text file of the repository.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or overwrite a file with the given content.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace exactly one occurrence of old_string with new_string in a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"},
                                                        "new_string": {"type": "string"}},
                       "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {
        "name": "run_tests", "description": "Run pytest in the repository; optional extra arguments such as a test path or -k expression.",
        "parameters": {"type": "object", "properties": {"args": {"type": "string", "description": "Extra pytest arguments"}}}}},
    {"type": "function", "function": {
        "name": "finish", "description": "Declare the task complete.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}
REQUIRED = {t["function"]["name"]: t["function"]["parameters"].get("required", []) for t in TOOLS}


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body[:300]}")
        self.status = status
        self.body = body

# ---------------------------------------------------------------- adapters

@dataclass
class Reply:
    content: str
    tool_calls: list[dict]      # [{"id", "name", "arguments": str}]
    usage: dict                 # normalised: input, output, reasoning, cache_read, cache_write
    raw_message: dict
    finish_reason: str


_REASONING_TAG = re.compile(r"<reasoning>.*?</reasoning>", re.S)


class OpenAIAdapter:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.http = httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0))

    def call(self, messages: list[dict]) -> Reply:
        body = {"model": self.model_id, "messages": messages, "tools": TOOLS, "tool_choice": "auto",
                "max_tokens": MAX_TOKENS}
        r = self.http.post(PROXY, json=body, headers={"content-type": "application/json"})
        if r.status_code != 200:
            raise ApiError(r.status_code, r.text)
        j = r.json()
        ch = j["choices"][0]
        msg = ch.get("message") or {}
        u = j.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        pdet = u.get("prompt_tokens_details") or {}
        usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
                 "reasoning": det.get("reasoning_tokens", 0) or 0,
                 "cache_read": pdet.get("cached_tokens", 0) or 0,
                 "cache_write": pdet.get("cache_write_tokens", 0) or 0}
        calls = [{"id": tc.get("id") or f"call_{i}", "name": (tc.get("function") or {}).get("name", ""),
                  "arguments": (tc.get("function") or {}).get("arguments", "")}
                 for i, tc in enumerate(msg.get("tool_calls") or [])]
        content = _REASONING_TAG.sub("", msg.get("content") or "").strip()
        return Reply(content, calls, usage, msg, ch.get("finish_reason", ""))

    @staticmethod
    def assistant_message(reply: Reply) -> dict:
        m: dict = {"role": "assistant", "content": reply.content or ""}
        if reply.tool_calls:
            m["tool_calls"] = [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": _replayable(c["arguments"])}}
                               for c in reply.tool_calls]
        return m


def _replayable(arguments: str) -> str:
    """Arguments as they go back into the history. Bedrock re-validates earlier tool calls on every request,
    so a malformed one would 400 every later turn; it is replayed as {} (the model is told in the tool result
    that its arguments were invalid, and the failure is counted in format_failures)."""
    try:
        json.loads(arguments or "{}")
        return arguments or "{}"
    except json.JSONDecodeError:
        return "{}"


class ConverseAdapter:
    def __init__(self, model_id: str, profile: str | None):
        import boto3
        from botocore.config import Config
        session = boto3.Session(profile_name=profile, region_name=REGION)
        self.client = session.client("bedrock-runtime", config=Config(read_timeout=600, retries={"max_attempts": 2}))
        self.model_id = model_id
        self.tool_config = {"tools": [{"toolSpec": {"name": t["function"]["name"],
                                                     "description": t["function"]["description"],
                                                     "inputSchema": {"json": t["function"]["parameters"]}}}
                                       for t in TOOLS]}

    def _convert(self, messages: list[dict]):
        system, out = [], []
        for m in messages:
            if m["role"] == "system":
                system.append({"text": m["content"]})
            elif m["role"] == "user":
                out.append({"role": "user", "content": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                blocks = [{"text": m["content"]}] if m.get("content") else []
                for tc in m.get("tool_calls") or []:
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append({"toolUse": {"toolUseId": tc["id"], "name": tc["function"]["name"], "input": args}})
                out.append({"role": "assistant", "content": blocks or [{"text": "(no content)"}]})
            elif m["role"] == "tool":
                block = {"toolResult": {"toolUseId": m["tool_call_id"], "content": [{"text": m["content"] or "(empty)"}]}}
                if out and out[-1]["role"] == "user" and all("toolResult" in b for b in out[-1]["content"]):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return system, out

    def call(self, messages: list[dict]) -> Reply:
        from botocore.exceptions import ClientError
        system, msgs = self._convert(messages)
        try:
            r = self.client.converse(modelId=self.model_id, system=system, messages=msgs,
                                     toolConfig=self.tool_config, inferenceConfig={"maxTokens": MAX_TOKENS})
        except ClientError as exc:
            raise ApiError(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0), str(exc)) from exc
        blocks = r["output"]["message"]["content"]
        text = "".join(b.get("text", "") for b in blocks)
        calls = [{"id": b["toolUse"]["toolUseId"], "name": b["toolUse"]["name"],
                  "arguments": json.dumps(b["toolUse"]["input"])} for b in blocks if "toolUse" in b]
        u = r.get("usage", {})
        cr, cw = u.get("cacheReadInputTokens", 0) or 0, u.get("cacheWriteInputTokens", 0) or 0
        usage = {"input": (u.get("inputTokens", 0) or 0) + cr + cw, "output": u.get("outputTokens", 0) or 0,
                 "reasoning": 0, "cache_read": cr, "cache_write": cw}
        return Reply(text, calls, usage, {"content": blocks}, r.get("stopReason", ""))

    assistant_message = staticmethod(OpenAIAdapter.assistant_message)


# ---------------------------------------------------------------- tools

class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, rel: str) -> Path:
        p = (self.root / (rel or ".")).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"path escapes the repository: {rel}")
        return p

    def list_files(self, path: str = ".") -> str:
        base = self._path(path)
        if not base.is_dir():
            return f"error: not a directory: {path}"
        files = sorted(str(p.relative_to(self.root)) for p in base.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts)
        return "\n".join(files) or "(empty)"

    def read_file(self, path: str) -> str:
        p = self._path(path)
        if not p.is_file():
            return f"error: no such file: {path}"
        return p.read_text()

    def write_file(self, path: str, content: str) -> str:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {path} ({len(content)} chars)"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        p = self._path(path)
        if not p.is_file():
            return f"error: no such file: {path}"
        text = p.read_text()
        n = text.count(old_string)
        if n != 1:
            return f"error: old_string found {n} times in {path}; it must match exactly once"
        p.write_text(text.replace(old_string, new_string, 1))
        return f"edited {path}"

    def run_tests(self, args: str = "") -> str:
        cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + (args.split() if args else [])
        try:
            r = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=TEST_TIMEOUT_S,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired:
            return f"error: tests timed out after {TEST_TIMEOUT_S}s"
        out = (r.stdout + r.stderr)[-6000:]
        return f"exit code {r.returncode}\n{out}"


# ---------------------------------------------------------------- loop

@dataclass
class RunResult:
    run_id: str
    model: str
    task: str
    rep: int
    stop: str = ""
    calls: int = 0
    tool_calls: int = 0
    format_failures: list = field(default_factory=list)
    no_tool_turns: int = 0
    api_errors: list = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0})
    usd: float = 0.0
    wall_s: float = 0.0
    visible_tests_run: int = 0


def run_agent(adapter, meter, model: str, task: dict, rep: int, repo_src: Path, work: Path, out_dir: Path) -> RunResult:
    run_id = f"{model}__{task['id']}__r{rep}"
    res = RunResult(run_id, model, task["id"], rep)
    repo = work / run_id / "repo"
    if repo.parent.exists():
        shutil.rmtree(repo.parent)
    shutil.copytree(repo_src, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    ws = Workspace(repo)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Repository: trialops (Python, pytest, synthetic data only).\n\nRequest: {task['prompt']}"}]
    transcript = []
    t0 = time.time()
    consecutive_idle = 0
    while True:
        if res.calls >= MAX_CALLS:
            res.stop = "max_calls"
            break
        if time.time() - t0 > MAX_WALL_S:
            res.stop = "wall_clock"
            break
        if not meter.allow():
            res.stop = "budget_hard_stop"
            break
        reply = None
        for attempt in range(4):
            try:
                reply = adapter.call(messages)
                break
            except (ApiError, httpx.HTTPError) as exc:
                status = getattr(exc, "status", 0)
                res.api_errors.append({"call": res.calls, "status": status, "error": str(exc)[:400]})
                if status in (401, 403) and "expired" in str(exc).lower():
                    res.stop = "credentials_expired"
                    break
                if status in (400, 404, 422) and attempt >= 1:
                    break
                time.sleep(5 * (attempt + 1) + (20 if status == 429 else 0))
        if reply is None:
            res.stop = res.stop or "api_error"
            break
        res.calls += 1
        for k in res.usage:
            res.usage[k] += reply.usage.get(k, 0) or 0
        res.usd += meter.record(run_id, model, reply.usage)
        transcript.append({"call": res.calls, "t": round(time.time() - t0, 2), "content": reply.content,
                           "tool_calls": reply.tool_calls, "usage": reply.usage, "finish_reason": reply.finish_reason})
        messages.append(adapter.assistant_message(reply))

        if not reply.tool_calls:
            res.no_tool_turns += 1
            consecutive_idle += 1
            if consecutive_idle >= 3:
                res.stop = "no_tool_calls"
                break
            messages.append({"role": "user", "content": "Continue using the tools. Call `finish` when the task is complete."})
            continue
        consecutive_idle = 0

        finished = False
        for tc in reply.tool_calls:
            res.tool_calls += 1
            name, raw = tc["name"], tc["arguments"] or "{}"
            result: str
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(args, dict):
                    raise ValueError("arguments are not a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                res.format_failures.append({"call": res.calls, "tool": name, "kind": "invalid_json", "detail": str(exc)[:200]})
                result = f"error: tool arguments are not valid JSON ({exc})"
            else:
                if name not in TOOL_NAMES:
                    res.format_failures.append({"call": res.calls, "tool": name, "kind": "unknown_tool"})
                    result = f"error: unknown tool {name!r}; available: {sorted(TOOL_NAMES)}"
                elif missing := [k for k in REQUIRED[name] if k not in args]:
                    res.format_failures.append({"call": res.calls, "tool": name, "kind": "missing_args", "detail": missing})
                    result = f"error: missing required arguments {missing}"
                elif name == "finish":
                    result = "ok"
                    finished = True
                else:
                    try:
                        result = getattr(ws, name)(**{k: v for k, v in args.items() if k in
                                                      TOOLS[[t["function"]["name"] for t in TOOLS].index(name)]["function"]["parameters"]["properties"]})
                    except Exception as exc:  # tool raised: report to the model
                        result = f"error: {type(exc).__name__}: {exc}"
                    if name == "run_tests":
                        res.visible_tests_run += 1
            transcript[-1].setdefault("results", []).append(result[:3000])
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result[:20000]})
        if finished:
            res.stop = "finish"
            break

    res.wall_s = round(time.time() - t0, 2)
    run_dir = out_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.json").write_text(json.dumps(transcript, indent=1, ensure_ascii=False))
    diff = subprocess.run(["diff", "-ruN", "-x", "__pycache__", "-x", ".pytest_cache", str(repo_src), str(repo)],
                          capture_output=True, text=True).stdout
    (run_dir / "diff.patch").write_text(diff.replace(str(repo_src), "a").replace(str(repo), "b"))
    return res


def grade(repo: Path, hidden_test: Path, repo_src: Path) -> dict:
    """Apply source changes to a pristine checkout and run authoritative tests.

    Agent-authored tests and pytest configuration never enter the grading
    checkout. Changes outside trialops production modules and test files are
    rejected. Hidden tests must execute at least one test without skips/xfails.
    """
    import tempfile

    def files(root: Path) -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and ".pytest_cache" not in path.parts
        }

    baseline = files(repo_src)
    actual = files(repo)
    changed = {path for path in baseline.keys() | actual.keys() if baseline.get(path) != actual.get(path)}
    invalid = sorted(
        str(path)
        for path in changed
        if not (
            (path.parts[0] == "trialops" and path.suffix == ".py")
            or (path.parts[0] == "tests" and path.name.startswith("test_") and path.suffix == ".py")
        )
    )
    if invalid:
        result = {"exit": 1, "passed": 0, "failed": 0, "errors": 1,
                  "tail": "unauthorized file changes: " + ", ".join(invalid)}
        return {"suite": result.copy(), "hidden": result.copy(), "invalid_paths": invalid}

    with tempfile.TemporaryDirectory(prefix="p117-grade-") as tmp:
        grading = Path(tmp) / "repo"
        shutil.copytree(repo_src, grading, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        for path in changed:
            if path.parts[0] != "trialops":
                continue
            destination = grading / path
            if path not in actual:
                destination.unlink(missing_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / path, destination)
        tests = grading / "tests"
        tests.mkdir(exist_ok=True)
        shutil.copy(hidden_test, tests / hidden_test.name)

        def run(target: str) -> dict:
            cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--override-ini", "addopts="] + ([target] if target else [])
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": ""}
            try:
                completed = subprocess.run(cmd, cwd=grading, capture_output=True, text=True, timeout=TEST_TIMEOUT_S, env=env)
                text, code = completed.stdout + completed.stderr, completed.returncode
            except subprocess.TimeoutExpired:
                text, code = "timeout", -1
            def count(pattern: str) -> int:
                match = re.search(pattern, text)
                return int(match.group(1)) if match else 0
            result = {
                "exit": code,
                "passed": count(r"(\d+) passed"),
                "failed": count(r"(\d+) failed"),
                "errors": count(r"(\d+) errors?"),
                "skipped": count(r"(\d+) skipped"),
                "xfailed": count(r"(\d+) xfailed"),
                "xpassed": count(r"(\d+) xpassed"),
                "tail": text[-1500:],
            }
            if result["passed"] == 0 or result["skipped"] or result["xfailed"] or result["xpassed"]:
                result["exit"] = 1
            return result

        hidden = run(f"tests/{hidden_test.name}")
        suite = run("")
        return {"hidden": hidden, "suite": suite}
