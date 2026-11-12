#!/usr/bin/env python3
"""CPU だけで動く埋め込みサーバ（vLLM の /v1/embeddings と同じ形）。常駐の vllm-embed-ruri が止まっている間の簡易検証用。

    python3 embed_server.py --model cl-nagoya/ruri-v3-310m --port 8000
標準ライブラリの http.server と sentence-transformers だけで動く（学習用イメージ embedding-trainer に入っている）。
入力はプレフィックスなしの生テキスト（backend の _get_vllm_embeddings と同じ）。
"""
import argparse, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="cl-nagoya/ruri-v3-310m")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--batch", type=int, default=16)
a = ap.parse_args()

from sentence_transformers import SentenceTransformer  # noqa: E402
import torch  # noqa: E402

torch.set_num_threads(max(1, torch.get_num_threads()))
model = SentenceTransformer(a.model, device="cpu")
lock = threading.Lock()
print(f"loaded {a.model} dim={model.get_sentence_embedding_dimension()} threads={torch.get_num_threads()}", flush=True)


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
        if self.path != "/v1/embeddings":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            inp = req.get("input")
            texts = [inp] if isinstance(inp, str) else list(inp or [])
            with lock:
                vecs = model.encode(texts, batch_size=a.batch, normalize_embeddings=True, convert_to_numpy=True)
            data = [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(vecs)]
            tok = sum(len(t) for t in texts)
            self._json(200, {"object": "list", "data": data, "model": a.model, "usage": {"prompt_tokens": tok, "total_tokens": tok}})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()
