"""今の変換・Nemotron Parse を、pdfium の文字層に対してページごとに比べる。

出力: compare.json(ページごとの指標と、落ちた行)と headings.md(見出しの並び)
指標:
  bag    文字層の文字のうち、出力に含まれる割合(順序は見ない。多重集合の共通部分 / 文字層の字数)
  seq    文字層の文字のうち、同じ順序で出力に現れる割合(difflib の一致ブロックの合計 / 文字層の字数)。bag との差が読み順の乱れ
  extra  出力の文字のうち、文字層に無いものの割合(多重集合)。読み誤り・付け足し・図の説明
字の正規化: NFKC、空白と点線(･・…‥.)の連続を除く、Markdown と HTML の印を除く
"""
import difflib, glob, html, json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/home/uchida/ai-stack/llm-graph-builder/backend/src/document_sources")
from number_headings import promote_number_headings  # noqa: E402 (読むだけ。純粋な正規表現の処理)

W = Path(sys.argv[1])
DOCS = ["VTR-T004C", "VTR-T005B", "HS-VTR-T002F"]
TAG = re.compile(r"<[^>]+>")
MD = re.compile(r"(\*\*|__|^#{1,6}\s*|^\s*[-*]\s+|\|---|\||\\)", re.M)
LEADER = re.compile(r"[･・…‥.\s]+")
WS = re.compile(r"\s+")


def norm(s):
    s = html.unescape(TAG.sub(" ", s or ""))
    s = MD.sub(" ", s)
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[･・…‥]{2,}|\.{3,}", "", s)
    return WS.sub("", s)


def metrics(ref, out):
    if not ref:
        return {}
    cr, co = Counter(ref), Counter(out)
    inter = sum((cr & co).values())
    sm = difflib.SequenceMatcher(None, ref, out, autojunk=False)
    seq = sum(b.size for b in sm.get_matching_blocks())
    return {"ref": len(ref), "out": len(out), "bag": round(inter / len(ref), 3), "seq": round(seq / len(ref), 3),
            "extra": round((len(out) - inter) / len(out), 3) if out else None}


def missing_lines(ref_text, out_norm, min_len=4):
    """文字層の行のうち、出力に(正規化して)部分文字列として見つからない行"""
    miss = []
    for line in re.split(r"[\r\n]+", ref_text):
        n = norm(line)
        if len(n) < min_len:
            continue
        if n in out_norm:
            continue
        # 行の 8 割以上が連続して一致すれば残ったとみなす
        sm = difflib.SequenceMatcher(None, n, out_norm, autojunk=False)
        m = sm.find_longest_match(0, len(n), 0, len(out_norm))
        if m.size >= 0.8 * len(n):
            continue
        miss.append(line.strip())
    return miss


def current_pages(stem):
    t = (W / "current" / f"{stem}.md").read_text(encoding="utf-8")
    parts = re.split(r"<!-- page:(\d+) -->", t)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def nemotron_pages(stem):
    res = {}
    for f in sorted(glob.glob(str(W / "nemotron" / stem / "0*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        page = int(re.search(r"_p(\d+)\.json$", f).group(1))
        res[page] = d
    return res


def heading_lines(md):
    out = []
    for line in md.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if m:
            out.append((len(m.group(1)), norm(m.group(2))[:40]))
    return out


result, hlines = {}, []
for stem in DOCS:
    tl = json.load(open(W / "textlayer" / f"{stem}.json", encoding="utf-8"))
    cur = current_pages(stem)
    nem = nemotron_pages(stem)
    frames = {}
    try:
        frames = {e["page"]: e for e in json.loads((W / "current" / f"{stem}.frames.json").read_text()).get("pages", [])}
    except Exception:
        pass
    hlines.append(f"\n## {stem}\n")
    for p in tl["pages"]:
        n = p["page"]
        ref_raw = p["text"]
        ref = norm(ref_raw)
        c_md = cur.get(n, "")
        nd = nem.get(n, {})
        blocks = nd.get("blocks", [])
        n_all = "\n\n".join(b.get("content") or b.get("text") or "" for b in blocks)
        n_body = "\n\n".join(b.get("content") or b.get("text") or "" for b in blocks if b["class"] not in ("Page-header", "Page-footer"))
        big = [(b["class"], round((b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]), 2)) for b in blocks
               if (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]) > 0.5]
        # 新しい経路が見出しの印を付けた後の Nemotron の Markdown(Section-header/Title は ##、他は本文として番号の規則をかける)
        n_md_lines = []
        for b in blocks:
            txt = (b.get("content") or b.get("text") or "").replace("**", "").strip()
            if b["class"] in ("Page-header", "Page-footer"):
                continue
            if b["class"] in ("Section-header", "Title"):
                n_md_lines.append("## " + txt)
            else:
                n_md_lines.append(txt)
        n_md_promoted = promote_number_headings("\n\n".join(n_md_lines))
        entry = {
            "frame_kind": frames.get(n, {}).get("kind"),
            "current": metrics(ref, norm(c_md)),
            "nemotron_all": metrics(ref, norm(n_all)),
            "nemotron_body": metrics(ref, norm(n_body)),
            "nemotron_big_regions": big,
            "nemotron_classes": Counter(b["class"] for b in blocks),
            "missing_current": missing_lines(ref_raw, norm(c_md)),
            "missing_nemotron_all": missing_lines(ref_raw, norm(n_all)),
            "headings_current": heading_lines(promote_number_headings(c_md)),
            "headings_nemotron_raw": [(b["class"], norm(b.get("text"))[:40]) for b in blocks if b["class"] in ("Section-header", "Title")],
            "headings_nemotron_promoted": heading_lines(n_md_promoted),
            "nemotron_page_headers": [norm(b.get("text"))[:40] for b in blocks if b["class"] in ("Page-header", "Page-footer")],
        }
        result[f"{stem} p{n}"] = entry
        hlines.append(f"\n### {stem} p{n}  (frame={entry['frame_kind']})\n")
        hlines.append("今の変換: " + " / ".join("#" * l + " " + t for l, t in entry["headings_current"]))
        hlines.append("\nNemotron 区分: " + " / ".join(f"[{c}] {t}" for c, t in entry["headings_nemotron_raw"]))
        hlines.append("\nNemotron+番号規則: " + " / ".join("#" * l + " " + t for l, t in entry["headings_nemotron_promoted"]))
        hlines.append("\nNemotron 柱: " + " / ".join(entry["nemotron_page_headers"]))
json.dump(result, open(W / "compare.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
(W / "headings.md").write_text("\n".join(hlines), encoding="utf-8")
print(f"{'page':<18}{'frame':<7}{'ref':>5} | {'cur bag':>7}{'seq':>6}{'extra':>6} | {'nem bag':>7}{'seq':>6}{'extra':>6} | nem big")
for k, e in result.items():
    c, a = e["current"], e["nemotron_all"]
    print(f"{k:<18}{str(e['frame_kind']):<7}{c.get('ref',0):>5} | {c.get('bag',0):>7}{c.get('seq',0):>6}{str(c.get('extra')):>6} | {a.get('bag',0):>7}{a.get('seq',0):>6}{str(a.get('extra')):>6} | {e['nemotron_big_regions']}")
