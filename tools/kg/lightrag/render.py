"""Graph Builder 既定モードと LightRAG の相違点のページを生成する（静的 HTML）。"""
import html
E = html.escape
INK, INK2, INK3, RULE = "#1B2430", "#4A5665", "#7A8794", "#C9D2DA"
ACC, ACC_SOFT = "#2a78d6", "#EAF2FC"

def box(x, y, w, h, header, lines, kind="plain"):
    stroke, fill, dash, tc, hc = RULE, "#FFFFFF", "", INK, INK3
    if kind == "acc":
        stroke, fill, hc = ACC, ACC_SOFT, "#1c5cab"
    if kind == "ghost":
        stroke, dash, tc = INK3, ' stroke-dasharray="5 4"', INK3
    if kind == "ans":
        stroke = INK
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="{1.5 if kind in ("acc","ans") else 1}"{dash}/>'
    s += f'<text x="{x + 10}" y="{y + 17}" font-size="10.5" fill="{hc}">{E(header)}</text>'
    for i, ln in enumerate(lines):
        s += f'<text x="{x + 10}" y="{y + 36 + i * 16}" font-size="12" fill="{tc}">{E(ln)}</text>'
    return s

def arrow(x1, y1, x2, y2, color=INK2, marker="ar"):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.5" marker-end="url(#{marker})"/>'

def chip(x, y, text, color=INK):
    w = 12 * len(text) + 12
    return (f'<rect x="{x}" y="{y}" width="{w}" height="18" rx="9" fill="{color}"/>'
            f'<text x="{x + w / 2}" y="{y + 13}" font-size="10.5" fill="#FFFFFF" text-anchor="middle">{E(text)}</text>')

C = [(14, 128), (172, 168), (370, 168), (568, 188), (786, 160)]
YA, YB, H = 36, 226, 76
svg = [f'<svg viewBox="0 0 960 400" role="img" aria-label="同じ質問に対する 2 方式の経路。Graph Builder の既定モードはチャンクを起点とし、エンティティの先のチャンク本文を取得しない。LightRAG はキーワードからエンティティを検索し、紐づく原文を取得する。">',
       '<defs>'
       f'<marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{INK2}"/></marker>'
       f'<marker id="arb" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{ACC}"/></marker>'
       '</defs>']
# レーン A: Graph Builder 既定モード
svg.append(f'<text x="14" y="22" font-size="13" font-weight="700" fill="{INK}">Graph Builder 既定モード（実測）</text>')
A = [("入力", ["吸込温度の点数と", "管理カーブの適用"], "plain"),
     ("チャンクを検索", ["T004C 1 番目", "T005B 1・4・8 番目", "T001E は 0 件"], "plain"),
     ("チャンクのエンティティ", ["空気吸込温度", "ほか"], "plain"),
     ("1 ホップ先のエンティティ", ["REQ-VTR-T001-p2-5", "説明文に「熱電対 2点」"], "ans"),
     ("LLM に渡す文脈", ["チャンク本文 4 件", "エンティティの名前と説明", "リレーションの行"], "plain")]
for (x, w), (hd, ls, k) in zip(C, A):
    svg.append(box(x, YA, w, H, hd, ls, k))
for i in range(4):
    svg.append(arrow(C[i][0] + C[i][1] + 2, YA + H / 2, C[i + 1][0] - 3, YA + H / 2))
gx, gw = C[3]
svg.append(f'<line x1="{gx + gw / 2}" y1="{YA + H}" x2="{gx + gw / 2}" y2="{YA + H + 12}" stroke="{INK3}" stroke-width="1.5" stroke-dasharray="4 3"/>')
svg.append(box(gx, YA + H + 12, gw, 50, "接続先のチャンク本文", ["T001E 2 番目は取得しない"], "ghost"))
svg.append(chip(gx + gw - 60, YA + H - 9, "答え 1"))
# レーン B: LightRAG
svg.append(f'<text x="14" y="{YB - 14}" font-size="13" font-weight="700" fill="{INK}">LightRAG（論文の手順を当てはめた場合）</text>')
B = [("入力", ["吸込温度の点数と", "管理カーブの適用"], "plain"),
     ("LLM がキーワードを抽出", ["吸込温度、管理カーブ、", "RU.RH.RH3.AT形過給機"], "plain"),
     ("エンティティを検索", ["空気吸込温度", "コンプレッサ性能管理曲線", "ほか"], "plain"),
     ("1 ホップ先の説明文", ["規定、計測機器、", "計測箇所など"], "plain"),
     ("LLM に渡す文脈", ["エンティティの説明", "リレーションの説明", "原文のチャンク"], "plain")]
for (x, w), (hd, ls, k) in zip(C, B):
    svg.append(box(x, YB, w, H, hd, ls, k))
for i in range(4):
    svg.append(arrow(C[i][0] + C[i][1] + 2, YB + H / 2, C[i + 1][0] - 3, YB + H / 2))
y4b = YB + H + 14
svg.append(box(gx, y4b, gw, 62, "紐づく原文を取得", ["T001E 2 番目", "T005B 8 番目 ほか"], "acc"))
ex = C[2][0] + C[2][1] / 2
svg.append(f'<polyline points="{ex},{YB + H + 2} {ex},{y4b + 31} {gx - 4},{y4b + 31}" fill="none" stroke="{ACC}" stroke-width="2" marker-end="url(#arb)"/>')
fx = C[4][0] + C[4][1] / 2
svg.append(f'<polyline points="{gx + gw + 2},{y4b + 31} {fx},{y4b + 31} {fx},{YB + H + 4}" fill="none" stroke="{ACC}" stroke-width="2" marker-end="url(#arb)"/>')
svg.append(chip(gx + gw - 60, y4b + 62 - 9, "答え 1", ACC))
svg.append("</svg>")

page = open("template.html", encoding="utf-8").read()
assert "__SVG__" in page
page = page.replace("__SVG__", "".join(svg))
open("lightrag-diff.html", "w", encoding="utf-8").write(page)
print("bytes", len(page.encode("utf-8")))
