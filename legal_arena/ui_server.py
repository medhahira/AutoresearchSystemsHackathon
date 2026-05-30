from __future__ import annotations

import asyncio
import cgi
import io
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _load_env_file() -> None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

from legal_arena.file_search import load_documents_from_paths
from legal_arena.modal_runtime import ModalRuntimeConfig
from legal_arena.structured_workflow import StructuredWorkflowResult, run_structured_workflow


HOST = os.getenv("LEGAL_ARENA_UI_HOST", "127.0.0.1")
PORT = int(os.getenv("LEGAL_ARENA_UI_PORT", "8000"))


@dataclass(slots=True)
class RunResult:
    prompt: str
    output: str
    error: str | None
    files: list[str]
    workflow_trace: str = ""


@dataclass(slots=True)
class JobState:
    prompt: str
    status: str
    result: RunResult | None = None


JOB_STATES: dict[str, JobState] = {}
JOB_LOCK = threading.Lock()


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Legal Arena</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: rgba(15, 23, 42, 0.92);
      --border: rgba(148, 163, 184, 0.2);
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #7dd3fc;
      --danger: #fb7185;
      --bubble-user: linear-gradient(135deg, #2563eb, #0ea5e9);
      --bubble-assistant: rgba(30, 41, 59, 0.96);
      --shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; background: radial-gradient(circle at top left, rgba(14,165,233,0.2), transparent 28%), radial-gradient(circle at top right, rgba(52,211,153,0.16), transparent 24%), var(--bg); color: var(--text); }}
    .shell {{ display: grid; grid-template-columns: minmax(320px, 420px) minmax(0, 1fr); min-height: 100vh; }}
    .sidebar {{ padding: 24px; border-right: 1px solid var(--border); background: linear-gradient(180deg, rgba(2,6,23,0.88), rgba(15,23,42,0.9)); backdrop-filter: blur(12px); }}
    .main {{ padding: 24px; }}
    .brand {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; }}
    .brand h1 {{ margin: 0; font-size: 24px; letter-spacing: -0.03em; }}
    .pill {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px; background: rgba(125, 211, 252, 0.12); color: var(--accent); border: 1px solid rgba(125, 211, 252, 0.2); font-size: 12px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 20px; box-shadow: var(--shadow); }}
    .section {{ padding: 18px; }}
    .section + .section {{ border-top: 1px solid var(--border); }}
    label {{ display: block; font-size: 13px; color: var(--muted); margin-bottom: 8px; }}
    textarea, input[type="number"] {{ width: 100%; border-radius: 14px; border: 1px solid var(--border); background: rgba(15, 23, 42, 0.95); color: var(--text); padding: 14px; outline: none; }}
    textarea {{ min-height: 170px; resize: vertical; line-height: 1.5; }}
    textarea:focus, input:focus {{ border-color: rgba(125, 211, 252, 0.5); box-shadow: 0 0 0 4px rgba(14,165,233,0.12); }}
    .row {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .checks {{ display: grid; gap: 10px; }}
    .check {{ display: flex; align-items: center; gap: 10px; color: var(--text); font-size: 14px; }}
    .check input {{ width: 16px; height: 16px; }}
    .button {{ width: 100%; border: 0; border-radius: 14px; padding: 14px 16px; background: linear-gradient(135deg, #0ea5e9, #22c55e); color: white; font-weight: 700; font-size: 15px; cursor: pointer; margin-top: 14px; }}
    .hint {{ color: var(--muted); font-size: 12px; line-height: 1.5; margin-top: 10px; }}
    .chat {{ display: grid; gap: 14px; }}
    .message {{ padding: 16px 18px; border-radius: 18px; border: 1px solid var(--border); background: var(--bubble-assistant); white-space: pre-wrap; line-height: 1.55; }}
    .message.user {{ background: var(--bubble-user); border-color: rgba(255,255,255,0.14); }}
    .message h2 {{ margin: 0 0 8px; font-size: 16px; }}
    .workflow {{ background: rgba(2,6,23,0.8); border: 1px solid var(--border); border-radius: 18px; padding: 14px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.6; color: #cbd5e1; white-space: pre-wrap; overflow-x: auto; }}
    .error {{ color: var(--danger); white-space: pre-wrap; }}
    .grid {{ display: grid; gap: 16px; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
    @media (max-width: 960px) {{ .shell {{ grid-template-columns: 1fr; }} .sidebar {{ border-right: 0; border-bottom: 1px solid var(--border); }} }}
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div>
          <h1>Legal Arena</h1>
          <div class="subtle">ChatGPT-style legal workflow demo</div>
        </div>
        <div class="pill">prompt + files + traces</div>
      </div>
      <form class="panel" method="post" enctype="multipart/form-data">
        <div class="section">
          <label for="prompt">Prompt</label>
          <textarea id="prompt" name="prompt" placeholder="Describe the legal issue...">{prompt}</textarea>
        </div>
        <div class="section">
          <label for="files">Files</label>
          <input id="files" name="files" type="file" multiple accept=".pdf,.txt,.md,.doc,.docx" />
          <div class="hint">Upload PDFs or text files. The app can optionally use OpenAI file search before case building.</div>
        </div>
        <div class="section">
          <div class="row">
            <div>
              <label for="rounds">Rounds</label>
              <input id="rounds" name="rounds" type="number" min="1" max="10" value="1" />
            </div>
            <div>
              <label for="use_file_search">Retrieval</label>
              <div class="checks" style="padding-top: 8px;">
                <label class="check"><input id="use_file_search" name="use_file_search" type="checkbox" checked />Use OpenAI file search</label>
                <label class="check"><input id="show_toolbox" name="show_toolbox" type="checkbox" checked />Show toolbox</label>
              </div>
            </div>
          </div>
          <button class="button" type="submit">Run workflow</button>
          <div class="hint">The right panel will show the live workflow transcript, stage logs, and final assessment.</div>
        </div>
      </form>
    </aside>
    <main class="main">
      <div class="grid">
        <div class="panel section">
          <h2 style="margin:0 0 8px;">Transcript</h2>
          <div class="chat">{conversation_html}</div>
        </div>
        <div class="panel section">
          <h2 style="margin:0 0 8px;">Workflow Trace</h2>
          <div class="workflow">{workflow_log}</div>
        </div>
      </div>
    </main>
  </div>
</body>
</html>
"""


def _render_message(title: str, body: str, kind: str = "assistant") -> str:
    return f'<div class="message {kind}"><h2>{escape(title)}</h2><div>{escape(body)}</div></div>'


def _render_result(result: RunResult | None) -> str:
    if result is None:
        return _render_message(
            "Ready",
            "Describe the legal issue, optionally upload files, and run the workflow.",
        )

    parts = [_render_message("Prompt", result.prompt, "user")]
    if result.files:
        parts.append(_render_message("Files", "\n".join(f"- {name}" for name in result.files)))
    if result.error:
        parts.append(_render_message("Error", result.error, "assistant"))
    parts.append(_render_message("Output", result.output or "(no output)", "assistant"))
    return "\n".join(parts)


def _format_source_result(source_result: object) -> str:
    source_type = getattr(source_result, "source_type", "unknown")
    query = getattr(source_result, "query", "")
    citations = getattr(source_result, "citations", []) or []
    raw_findings = getattr(source_result, "raw_findings", "") or ""
    error = getattr(source_result, "error", None)
    chunks = [f"Source: {source_type}", f"Query: {query}"]
    if citations:
        chunks.append("Citations:\n" + "\n".join(f"- {citation}" for citation in citations[:5]))
    if raw_findings:
        chunks.append("Findings:\n" + raw_findings[:3000])
    if error:
        chunks.append(f"Error: {error}")
    return "\n\n".join(chunks)


def _format_turn_trace(workflow: StructuredWorkflowResult | None) -> str:
    if workflow is None:
        return ""

    sections: list[str] = []
    if workflow.source_packets:
        sections.append("=== CourtListener / Source Packets ===")
        for packet in workflow.source_packets:
            sections.append(f"Round {packet.round_number} {packet.side}")
            for source_result in packet.source_results:
                sections.append(_format_source_result(source_result))
            sections.append(
                "Synthesized excerpts:\n" + (packet.synthesized_sources.relevant_excerpts or "(none)")
            )

    if workflow.turn_traces:
        sections.append("=== Debate Turns ===")
        for turn in workflow.turn_traces:
            sections.append(
                "\n".join(
                    [
                        f"Round {turn.round_number} {turn.side}",
                        "Argument:\n" + turn.argument.argument,
                        "Key points:\n" + "\n".join(f"- {point}" for point in turn.argument.key_points),
                        f"Judge score: {turn.judgment.total_score}/100",
                        "Judge rationale:\n" + turn.judgment.rationale,
                    ]
                )
            )

    if workflow.final_assessment is not None:
        assessment = workflow.final_assessment
        sections.append("=== Final Assessment ===")
        sections.append(
            "\n".join(
                [
                    f"Risk score: {assessment.risk_score}/10",
                    f"Recommendation: {assessment.settle_recommendation}",
                    f"Rationale: {assessment.settle_rationale}",
                ]
            )
        )

    return "\n\n".join(sections)


def _render_job_page(job_state: JobState | None) -> str:
    if job_state is None:
        return _render_result(None)
    if job_state.status == "running":
        return "\n".join(
            [
                _render_message("Prompt", job_state.prompt, "user"),
                _render_message(
                    "Status",
                    "Workflow is running in the background. This page refreshes automatically.",
                ),
                '<meta http-equiv="refresh" content="2">',
            ]
        )
    if job_state.result is not None:
        return _render_result(job_state.result)
    return _render_result(None)


class LegalArenaUIHandler(BaseHTTPRequestHandler):
    server_version = "LegalArenaUI/0.2"

    def do_GET(self) -> None:
        if urlparse(self.path).path not in {"/", "/index.html"}:
            self.send_error(404)
            return

        query = parse_qs(urlparse(self.path).query)
        job_id = query.get("job", [""])[0]
        with JOB_LOCK:
            job_state = JOB_STATES.get(job_id) if job_id else None
        self._send_html(_render_job_page(job_state), "")

    def do_POST(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self.send_error(400, "Expected multipart/form-data")
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
        )
        prompt = str(form.getfirst("prompt", "")).strip()
        rounds_raw = str(form.getfirst("rounds", "1")).strip()
        use_file_search = form.getfirst("use_file_search") is not None
        show_toolbox = form.getfirst("show_toolbox") is not None

        try:
            rounds = max(1, min(10, int(rounds_raw)))
        except ValueError:
            rounds = 1

        uploaded_files = self._save_uploads(form)
        job_id = uuid.uuid4().hex[:8]
        with JOB_LOCK:
            JOB_STATES[job_id] = JobState(prompt=prompt, status="running")

        threading.Thread(
            target=self._run_job,
            daemon=True,
            args=(job_id, prompt, uploaded_files, rounds, show_toolbox, use_file_search),
        ).start()

        self.send_response(303)
        self.send_header("Location", f"/?job={job_id}")
        self.end_headers()

    @staticmethod
    def _run_job(
        job_id: str,
        prompt: str,
        uploaded_files: list[Path],
        rounds: int,
        show_toolbox: bool,
        use_file_search: bool,
    ) -> None:
        captured = io.StringIO()
        error: str | None = None
        workflow: StructuredWorkflowResult | None = None
        try:
            documents, traces = load_documents_from_paths(
                uploaded_files,
                use_file_search=use_file_search,
                query=prompt,
            )
            if show_toolbox:
                captured = io.StringIO()
                with redirect_stdout(captured):
                    case_input = {
                        "prompt": prompt,
                        "documents": [document.title for document in documents],
                        "traces": traces,
                    }
                    print("=== Toolbox ===")
                    print(f"Prompt: {prompt}")
                    print("Input processing:")
                    for trace in traces:
                        print(f"- {trace}")
                    print("Loaded documents:")
                    for document in documents:
                        print(f"- {document.title} ({document.source or 'inline'})")
                    print("=== End Toolbox ===")
                toolbox_output = captured.getvalue().strip()
            else:
                toolbox_output = ""

            workflow = asyncio.run(
                run_structured_workflow(
                    problem_statement=prompt or "Review the uploaded legal documents and assess the case.",
                    documents=documents,
                    n_rounds=rounds,
                    modal_config=ModalRuntimeConfig.from_env(),
                )
            )
        except Exception as exc:
            error = f"Workflow failed: {exc}"


        output = _format_turn_trace(workflow)
        if show_toolbox and 'toolbox_output' in locals() and toolbox_output:
            output = f"{toolbox_output}\n\n{output}".strip()
        if error:
            output = f"{output}\n\n{error}".strip()

        result = RunResult(
            prompt=prompt,
            output=output,
            error=error,
            files=[path.name for path in uploaded_files],
            workflow_trace=output,
        )
        with JOB_LOCK:
            JOB_STATES[job_id] = JobState(
                prompt=prompt,
                status="error" if error else "done",
                result=result,
            )

        for path in uploaded_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _send_html(self, conversation_html: str, workflow_log: str) -> None:
        html = HTML_TEMPLATE.format(
            prompt="",
            conversation_html=conversation_html,
            workflow_log=escape(workflow_log),
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _save_uploads(self, form: cgi.FieldStorage) -> list[Path]:
        uploaded: list[Path] = []
        files_field = form["files"] if "files" in form else None
        if files_field is None:
            return uploaded

        items = files_field if isinstance(files_field, list) else [files_field]
        temp_dir = Path(tempfile.mkdtemp(prefix="legal-arena-ui-"))
        for item in items:
            filename = os.path.basename(item.filename or "upload.bin")
            destination = temp_dir / filename
            with destination.open("wb") as handle:
                shutil.copyfileobj(item.file, handle)
            uploaded.append(destination)
        return uploaded


def run_server() -> None:
    server = ThreadingHTTPServer((HOST, PORT), LegalArenaUIHandler)
    print(f"Legal Arena UI running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
