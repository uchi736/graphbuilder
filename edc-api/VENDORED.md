# EDC の取り込み元

このフォルダは EDC（Extract-Define-Canonicalize）の API の複製である。直接は編集せず、取り込み元を直してから
`scripts/sync_edc.sh <EDC の checkout>` で取り込み直す。

| 項目 | 値 |
|---|---|
| 取り込み元 | https://github.com/uchi736/EDC.git |
| コミット | 788b8504306c20e9b47aa41311adc0b9f6ab38b8 |
| コミットの日付 | 2026-11-17 |
| ライセンス | MIT（LICENSE） |

文書タイプとスキーマは `schemas/`（`registry.json` が一覧）。compose がこのフォルダを edc-api と backend の両方に
読み取り専用でマウントするので、追加・変更はイメージを作り直さずに反映される。
