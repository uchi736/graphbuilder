#!/usr/bin/env python3
"""CPU だけで動くリランカー（vLLM の /v1/rerank と同じ形）。常駐の vllm-reranker が止まっている間の簡易検証用。

    python3 rerank_server.py --model cl-nagoya/ruri-v3-reranker-310m --port 8000
応答: {"results": [{"index": i, "relevance_score": s}, ...]}（backend の _rerank が読む形）。点数は sigmoid で 0〜1。
"""
import argparse, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="cl-nagoya/ruri-v3-reranker-310m")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--max-length", type=int, default=1024)
a = ap.parse_args()

from sentence_transformers import CrossEncoder  # noqa: E402
import torch  # noqa: E402

model = CrossEncoder(a.model, device="cpu", max_length=a.max_length)
lock = threading.Lock()
print(f"loaded {a.model} threads={torch.get_num_threads()}", flush=True)


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/v1/models"):
            return self._json(200, {"status": "ok", "data": [{"id": a.model, "object": "model"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/v1/rerank", "/rerank"):
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            q = req.get("query") or ""
            docs = req.get("documents") or []
            with lock:
                scores = model.predict([(q, d or "-") for d in docs], batch_size=16, activation_fct=torch.nn.Sigmoid(), show_progress_bar=False)
            results = [{"index": i, "relevance_score": float(s), "document": {"text": docs[i]}} for i, s in enumerate(scores)]
            results.sort(key=lambda r: -r["relevance_score"])
            self._json(200, {"id": "cpu", "model": a.model, "results": results, "usage": {"total_tokens": 0}})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()
