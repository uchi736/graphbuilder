#!/usr/bin/env python3
"""Azure OpenAI（GPT-6 Luna）への中継。OpenAI 互換の /v1/chat/completions を受け、使った額を数え、上限で止める。

    python3 luna_meter.py --port 8000 --cap 0.90 --ledger /ledger/luna_usage.json
接続情報は環境変数（~/ai-stack/secrets/azure-openai.env をそのまま --env-file で渡す）:
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION, PRICE_IN, PRICE_OUT, PRICE_CACHED
要求の読み替え（Luna は推論モデルなので）:
    model → 配置名 / max_tokens → max_completion_tokens（最低 LUNA_MIN_COMPLETION=16000、思考の分）/ temperature・top_p などは外す / reasoning_effort が無ければ LUNA_EFFORT（既定 low）
    stream=true は、まとめて受けてから 1 回の SSE で返す
上限: 累計の額（USD）が --cap 以上になったら、以降は 429 を返す。台帳は呼び出しごとに書く（中継を立て直しても累計を引き継ぐ）。
"""
import argparse, json, os, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--cap", type=float, default=0.90)
ap.add_argument("--ledger", default="/ledger/luna_usage.json")
a = ap.parse_args()

EP = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
KEY = os.environ["AZURE_OPENAI_API_KEY"]
DEP = os.environ["AZURE_OPENAI_DEPLOYMENT"]
VER = os.environ.get("AZURE_OPENAI_API_VERSION", "v1")
PIN, POUT, PCACHE = (float(os.environ.get(k, d)) for k, d in (("PRICE_IN", "0.10"), ("PRICE_OUT", "0.50"), ("PRICE_CACHED", "0.01")))
EFFORT = os.environ.get("LUNA_EFFORT", "low")
# 出力の下限。Luna は思考の分も出力に数えるので、要求の max_tokens（Graph Builder は 4096）だと思考が使い切って本文が切れることがある
MIN_COMPLETION = int(os.environ.get("LUNA_MIN_COMPLETION", "16000"))
URL = f"{EP}/openai/v1/chat/completions" if VER == "v1" else f"{EP}/openai/deployments/{DEP}/chat/completions?api-version={VER}"
DROP = ("temperature", "top_p", "presence_penalty", "frequency_penalty", "logprobs", "top_logprobs", "n", "stop", "seed",
        "repetition_penalty", "top_k", "min_p", "chat_template_kwargs", "guided_json", "guided_choice")

lock = threading.Lock()
try:
    state = json.load(open(a.ledger))
except Exception:
    state = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "usd": 0.0, "refused": 0, "errors": 0, "by_client": {}}


def save():
    tmp = a.ledger + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.ledger)


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/v1/models", "/usage"):
            with lock:
                return self._json(200, {"status": "ok", "data": [{"id": DEP, "object": "model"}], "usage": state, "cap_usd": a.cap})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": "only /v1/chat/completions"})
        client = self.headers.get("X-Client", self.client_address[0])
        with lock:
            if state["usd"] >= a.cap:
                state["refused"] += 1; save()
                return self._json(429, {"error": {"message": f"luna-meter: 上限 {a.cap} USD に達した（累計 {state['usd']:.4f}）", "type": "budget_exceeded"}})
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        stream = bool(req.pop("stream", False)); req.pop("stream_options", None)
        for k in DROP:
            req.pop(k, None)
        mt = req.pop("max_tokens", None) or req.get("max_completion_tokens") or 4000
        req["max_completion_tokens"] = max(int(mt), MIN_COMPLETION)
        req.setdefault("reasoning_effort", EFFORT)
        if VER == "v1":
            req["model"] = DEP
        else:
            req.pop("model", None)
        data = json.dumps(req).encode()
        t0 = time.time()
        for attempt in range(4):
            try:
                r = urllib.request.urlopen(urllib.request.Request(URL, data=data, method="POST",
                                           headers={"Content-Type": "application/json", "api-key": KEY}), timeout=300)
                body = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", "replace")
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 * (attempt + 1)); continue
                with lock:
                    state["errors"] += 1; save()
                return self._json(e.code, {"error": {"message": err[:800]}})
            except Exception as e:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1)); continue
                with lock:
                    state["errors"] += 1; save()
                return self._json(502, {"error": {"message": str(e)}})
        u = body.get("usage") or {}
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        usd = ((pt - cached) * PIN + cached * PCACHE + ct * POUT) / 1e6
        with lock:
            state["calls"] += 1; state["prompt_tokens"] += pt; state["cached_tokens"] += cached
            state["completion_tokens"] += ct; state["reasoning_tokens"] += reasoning; state["usd"] = round(state["usd"] + usd, 6)
            c = state["by_client"].setdefault(client, {"calls": 0, "usd": 0.0})
            c["calls"] += 1; c["usd"] = round(c["usd"] + usd, 6)
            save()
        print(f"{time.strftime('%H:%M:%S')} {client} in={pt} out={ct}(think {reasoning}) ${usd:.5f} total=${state['usd']:.4f} {time.time()-t0:.1f}s", flush=True)
        if not stream:
            return self._json(200, body)
        # stream を求められた場合は、1 回の SSE で返す
        msg = (body.get("choices") or [{}])[0].get("message", {})
        chunk = {"id": body.get("id"), "object": "chat.completion.chunk", "created": body.get("created"), "model": body.get("model"),
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": msg.get("content", "")}, "finish_reason": (body.get("choices") or [{}])[0].get("finish_reason")}],
                 "usage": u}
        out = ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode()
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass


print(f"luna-meter → {URL}  cap ${a.cap}  effort {EFFORT}  start ${state['usd']:.4f}", flush=True)
ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()
