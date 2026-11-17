#!/usr/bin/env bash
# backend/tokenizers（git の追跡外）を用意する。導入時に 1 回だけ実行する。
# このスクリプトだけが社外（huggingface.co と openaipublic.blob.core.windows.net）へ通信する。実行時の通信をなくすための準備である。
# 社外へ出られない環境では、出られる環境で作った backend/tokenizers をフォルダごと運ぶ。
#
#   scripts/fetch_tokenizers.sh [埋め込みモデルの Hugging Face の名前（既定 cl-nagoya/ruri-v3-310m）]
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:-cl-nagoya/ruri-v3-310m}"
DEST_TOK="backend/tokenizers/$(basename "$MODEL")"
DEST_TT="backend/tokenizers/tiktoken-cache"
mkdir -p "$DEST_TOK" "$DEST_TT"

fetch() {  # fetch <URL> <保存先>
  if [ -s "$2" ]; then echo "あり    $2"; return; fi
  echo "取得    $1"
  curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}

# チャンクの長さを数えるトークナイザー（CHUNK_TOKENIZER が指す）
fetch "https://huggingface.co/${MODEL}/resolve/main/tokenizer.json" "$DEST_TOK/tokenizer.json"

# tiktoken の gpt2 の符号化ファイル（TIKTOKEN_CACHE_DIR が指す）。ファイル名は取得元の URL の sha1
for f in vocab.bpe encoder.json; do
  url="https://openaipublic.blob.core.windows.net/gpt-2/encodings/main/$f"
  fetch "$url" "$DEST_TT/$(printf '%s' "$url" | sha1sum | cut -d' ' -f1)"
done

echo "完了: $DEST_TOK/tokenizer.json と $DEST_TT の 2 ファイル"
[ "$MODEL" = "cl-nagoya/ruri-v3-310m" ] || echo "注意: .env に CHUNK_TOKENIZER=/code/tokenizers/$(basename "$MODEL")/tokenizer.json を書く"
