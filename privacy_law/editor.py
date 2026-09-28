"""개인정보 문구 수정 화면 (관리자 PC 전용, 브라우저).

    python -m privacy_law.editor          (또는 editor.bat 더블클릭)

texts/ 의 문서를 고르고 → 원문(HTML) 수정 + 실시간 미리보기 → 저장 → [GitHub 반영] 으로 커밋 · push.
이 PC(127.0.0.1)에서만 열리고, 실행할 때마다 새 접속 토큰을 만든다(다른 사이트가 몰래 저장 요청을 못 보내게).
"""
from __future__ import annotations

import datetime
import json
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import DEFAULT_DIR

REPO_DIR = DEFAULT_DIR.parent.parent
TOKEN = secrets.token_urlsafe(24)
VERSION_RE = re.compile(r"^[0-9A-Za-z.\-]{1,32}$")
# 미리보기에 채울 예시 값 (실제 값은 각 서비스가 넘김)
SAMPLE = {"service_name": "Engineering Management Solution", "service_desc": "공공 공사 설계서 자동화 시스템",
          "collect_items": "아이디, 비밀번호, 이메일, 본인인증 정보(성명, 휴대폰 번호, 연계정보 CI)"}


def load_docs() -> list[dict]:
    return json.loads((DEFAULT_DIR / "documents.json").read_text(encoding="utf-8"))


def save_docs(items: list[dict]) -> None:
    # 원래 모양(한 문서 한 줄) 유지 → git 변경 내역을 보기 쉽게
    lines = ",\n".join("  " + json.dumps(i, ensure_ascii=False) for i in items)
    (DEFAULT_DIR / "documents.json").write_text(f"[\n{lines}\n]\n", encoding="utf-8")


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=REPO_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def state() -> dict:
    docs = load_docs()
    for d in docs:
        d["html"] = (DEFAULT_DIR / d["file"]).read_text(encoding="utf-8").replace("\r\n", "\n") if d.get("file") else None
    _, changed = git("status", "--short", "--", "privacy_law/texts")
    _, remote = git("remote", "get-url", "origin")
    _, last = git("log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M")
    return {"docs": docs, "sample": SAMPLE, "changed": changed, "remote": remote, "last": last,
            "today": datetime.date.today().isoformat()}


def save(body: dict) -> str:
    items = load_docs()
    item = next((i for i in items if i["key"] == body.get("key")), None)
    if item is None:
        raise ValueError("없는 문서입니다.")
    title = str(body.get("title", "")).strip()
    version = str(body.get("version", "")).strip()
    if not title:
        raise ValueError("제목을 입력하세요.")
    if not VERSION_RE.match(version):
        raise ValueError("버전은 영문 · 숫자 · 점 · 하이픈만 (예: 2027-01-01)")
    item.update(title=title, required=bool(body.get("required")), version=version)
    if item.get("file") and body.get("html") is not None:
        html = str(body["html"]).replace("\r\n", "\n")
        (DEFAULT_DIR / item["file"]).write_text(html if html.endswith("\n") else html + "\n", encoding="utf-8")
    save_docs(items)
    return f"'{title}' 저장됨 — 서비스 화면에는 다음 요청부터 바로 반영됩니다."


def publish(body: dict) -> str:
    message = str(body.get("message", "")).strip() or "docs: 개인정보 문구 개정"
    code, out = git("add", "--", "privacy_law/texts")
    if code:
        raise ValueError(out)
    if git("diff", "--cached", "--quiet")[0] == 0:
        raise ValueError("바뀐 내용이 없습니다. 먼저 저장하세요.")
    code, out = git("commit", "-m", message)
    if code:
        raise ValueError(out)
    code, pushed = git("push", "-u", "origin", "HEAD")
    if code:
        raise ValueError("커밋은 되었지만 GitHub 올리기 실패:\n" + pushed)
    return "GitHub 반영 완료 — 각 서비스 서버에서 git pull 하면 적용됩니다.\n" + out.splitlines()[0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):   # 콘솔 조용히
        pass

    def _allowed(self) -> bool:
        host = self.headers.get("Host", "")
        return host.startswith(("127.0.0.1:", "localhost:"))

    def _send(self, code: int, data, ctype="application/json; charset=utf-8"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        if self.path == f"/?t={TOKEN}":
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN).encode("utf-8"), "text/html; charset=utf-8")
        if self.path == "/api/state" and self.headers.get("X-Token") == TOKEN:
            return self._send(200, state())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._allowed() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
            if self.path == "/api/save":
                return self._send(200, {"ok": save(body)})
            if self.path == "/api/publish":
                return self._send(200, {"ok": publish(body)})
            self._send(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>개인정보 문구 수정</title>
<style>
  :root { --bg:#F5F6F8; --panel:#fff; --line:#DDE1E6; --ink:#1F2933; --mute:#6B7480; --accent:#2F5BD3; --ok:#177245; --err:#B42318; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Malgun Gothic','맑은 고딕',-apple-system,sans-serif; background:var(--bg); color:var(--ink); }
  header { padding:14px 20px; background:var(--panel); border-bottom:1px solid var(--line); display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:18px; margin:0; }
  header small { color:var(--mute); }
  main { display:grid; grid-template-columns:220px 1fr; gap:16px; padding:16px 20px; }
  nav button { display:block; width:100%; text-align:left; padding:10px 12px; margin-bottom:6px; border:1px solid var(--line);
               background:var(--panel); border-radius:6px; cursor:pointer; font:inherit; }
  nav button.on { border-color:var(--accent); background:#EEF2FC; font-weight:700; }
  nav .tag { font-size:11px; color:var(--mute); display:block; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }
  .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
  label { font-size:13px; color:var(--mute); }
  input[type=text] { font:inherit; padding:7px 9px; border:1px solid var(--line); border-radius:6px; }
  .split { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  textarea { width:100%; height:62vh; font:13px/1.55 Consolas,monospace; padding:10px; border:1px solid var(--line); border-radius:6px; resize:vertical; }
  iframe { width:100%; height:62vh; border:1px solid var(--line); border-radius:6px; background:#F8F9FA; }
  .btn { font:inherit; padding:8px 14px; border-radius:6px; border:1px solid var(--line); background:var(--panel); cursor:pointer; }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  .hint { font-size:12px; color:var(--mute); margin:4px 0 0; }
  #msg { white-space:pre-wrap; font-size:13px; margin-top:10px; min-height:1em; }
  #msg.ok { color:var(--ok); } #msg.err { color:var(--err); }
  .publish { margin-top:16px; }
  .publish input { flex:1; min-width:240px; }
  pre.changed { font-size:12px; background:#F5F6F8; padding:8px; border-radius:6px; margin:6px 0 0; white-space:pre-wrap; }
  .vars code { background:#F0F2F5; padding:1px 5px; border-radius:4px; font-size:12px; }
  @media (max-width: 900px) { main, .split { grid-template-columns:1fr; } }
</style></head>
<body>
<header><h1>개인정보 문구 수정</h1><small id="repo"></small></header>
<main>
  <nav id="list"></nav>
  <section>
    <div class="panel">
      <div class="row">
        <label>제목 <input type="text" id="title" size="22"></label>
        <label><input type="checkbox" id="required"> 필수 동의</label>
        <label>버전 <input type="text" id="version" size="12"></label>
        <button class="btn" id="bump">오늘 날짜로</button>
      </div>
      <p class="hint">내용(수집 항목 · 목적 · 보유기간 등)이 바뀌면 버전을 올리세요 → 기존 회원 재동의 대상이 됩니다. 오타 수정은 그대로 두어도 됩니다.</p>
      <p class="hint vars">서비스가 채우는 값: <code>{{ service_name }}</code> <code>{{ service_desc }}</code> <code>{{ collect_items }}</code> (미리보기는 예시 값)</p>
      <div class="split" id="editor">
        <textarea id="html" spellcheck="false"></textarea>
        <iframe id="preview" sandbox title="미리보기"></iframe>
      </div>
      <p class="hint" id="nofile" hidden>이 문서는 본문 없이 체크박스만 표시됩니다(제목 · 필수 여부 · 버전만 수정).</p>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="save">저장</button></div>
      <div id="msg"></div>
    </div>
    <div class="panel publish">
      <div class="row">
        <input type="text" id="message" placeholder="변경 내용 (예: 개인정보 보호법 개정 반영 — 보유기간 수정)">
        <button class="btn primary" id="publish">GitHub 반영 (커밋 · push)</button>
      </div>
      <div class="hint">아직 GitHub 에 올리지 않은 변경:</div>
      <pre class="changed" id="changed"></pre>
    </div>
  </section>
</main>
<script>
const TOKEN = "__TOKEN__";
let S = null, cur = null;
const $ = id => document.getElementById(id);
const CSS = `<style>body{font-family:'Malgun Gothic',sans-serif;font-size:14px;color:#333;line-height:1.8;padding:16px;margin:0;background:#F8F9FA}
h3{font-size:16px;font-weight:700;color:#111;margin:16px 0 8px}p{margin:0 0 12px}
.consent-table{width:100%;border-collapse:collapse;margin-bottom:10px;background:#fff}
.consent-table th,.consent-table td{border:1px solid #D1D5DB;padding:8px;text-align:left;vertical-align:top;font-size:13px;line-height:1.7}
.consent-table th{width:30%;background:#F3F4F6;color:#111;font-weight:700;white-space:nowrap}
p.consent-note{font-size:13px;color:#B91C1C;margin:0}</style>`;

async function api(path, body) {
  const r = await fetch(path, body ? {method:"POST", headers:{"X-Token":TOKEN,"Content-Type":"application/json"}, body:JSON.stringify(body)}
                                   : {headers:{"X-Token":TOKEN}});
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.status);
  return d;
}
function say(text, ok) { $("msg").textContent = text; $("msg").className = ok ? "ok" : "err"; }
function fill(html) { return html.replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) => S.sample[k] ?? m); }
function preview() { $("preview").srcdoc = CSS + fill($("html").value); }
function dirty() { const d = S.docs.find(x => x.key === cur); return d && (d.html ?? "") !== ($("html").value) && d.file; }

function render() {
  $("repo").textContent = (S.remote || "GitHub 원격 저장소 없음") + (S.last ? "  ·  마지막 커밋 " + S.last : "");
  $("changed").textContent = S.changed || "(없음)";
  $("list").innerHTML = "";
  for (const d of S.docs) {
    const b = document.createElement("button");
    b.className = d.key === cur ? "on" : "";
    b.innerHTML = `${d.title}<span class="tag">${d.required ? "필수" : "선택"} · v${d.version}</span>`;
    b.onclick = () => { if (dirty() && !confirm("저장하지 않은 수정이 있습니다. 버릴까요?")) return; select(d.key); };
    $("list").appendChild(b);
  }
}
function select(key) {
  cur = key; const d = S.docs.find(x => x.key === key);
  $("title").value = d.title; $("required").checked = !!d.required; $("version").value = d.version;
  $("editor").hidden = !d.file; $("nofile").hidden = !!d.file;
  $("html").value = d.html ?? ""; preview(); say("", true); render();
}
async function load(key) { S = await api("/api/state"); select(key || cur || S.docs[0].key); }

$("html").addEventListener("input", preview);
$("bump").onclick = () => { $("version").value = S.today; };
$("save").onclick = async () => {
  const d = S.docs.find(x => x.key === cur);
  if (d.file && $("html").value !== d.html && $("version").value === d.version &&
      !confirm("본문이 바뀌었는데 버전은 그대로입니다.\n오타 수정처럼 재동의가 필요 없는 변경이면 [확인], 버전을 올리려면 [취소]")) return;
  try { const r = await api("/api/save", {key:cur, title:$("title").value, required:$("required").checked,
                                          version:$("version").value, html:d.file ? $("html").value : null});
        await load(cur); say(r.ok, true); } catch (e) { say(e.message, false); }
};
$("publish").onclick = async () => {
  if (dirty() && !confirm("저장하지 않은 수정은 빠집니다. 계속할까요?")) return;
  $("publish").disabled = true;
  try { const r = await api("/api/publish", {message:$("message").value}); $("message").value = ""; await load(cur); say(r.ok, true); }
  catch (e) { say(e.message, false); } finally { $("publish").disabled = false; }
};
window.addEventListener("beforeunload", e => { if (dirty()) e.preventDefault(); });
load().catch(e => say("불러오기 실패: " + e.message, false));
</script>
</body></html>
"""


def main(port: int = 0) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={TOKEN}"
    print(f"개인정보 문구 수정 화면: {url}\n(창을 닫아도 이 콘솔을 닫기 전까지 열려 있습니다. 종료: Ctrl+C)")
    threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
