# 数え直しのエンティティ照合（方式の持ち方に依らない数え方）。標準ライブラリのみ。
# ── 規則 ──────────────────────────────────────────────────────────────
#  正規化 : NFKC（全角→半角、半角カナ→全角）、空白除去、大文字化、（最新版）等の除去は score_gold.py と同じ。
#           数え直しでは加えて、長音とハイフン類を 1 つに、波ダッシュ類を 1 つにそろえる。
#  鍵     : 正解の id と別名を正規化したもの。
#  出現   : ノードの名前（props.id）の中に鍵が現れる。ただし
#            ・鍵の端が英字なら隣が英字でない、鍵の端が数字なら隣が数字（前は小数点も）でない（AT が ATM に、2℃ が 12℃ に当たらない）。
#            ・英数字だけの鍵は 2 文字から、それ以外の鍵は 3 文字から。それより短い鍵は名前全体の一致だけ。
#  覆い   : 別の正解の物の、より長い鍵の出現に完全に含まれる出現は数えない
#           （「コンプレッサ出口」は コンプレッサ出口 の物で コンプレッサ ではない。「RH3」は RH ではない。「検査部門長」は 検査部門 ではない）。
#  総称   : 他の正解の物 3 つ以上の id に含まれる語（過給機、コンプレッサ）は、「別の正解の物も含むか」の判定では数えない。
#  番号だけのノード : 名前が機械的に振った番号のノード（REQ- で始まる規定のノード）は、名前を持たないものとして照合から外す
#           （関係の中継と、値の置き場としては使う）。
#  段階（1 つのノードと 1 つの正解の物の組に対し、上から順に 1 つだけ付く）
#    strict   その物のノード : 名前が鍵に一致、または鍵の前後に 3 文字までの伸び（いまの数え方と同じ幅）、または末尾の括弧書きを除くとそうなる。
#    modified 修飾つきの名前 : 覆われていない出現があり、別の正解の物を含まず、鍵が名前の長さの半分以上。
#    sentence 文の形         : 覆われていない出現があり、別の正解の物を含まず、鍵が名前の長さの半分未満。
#    compound 複合の名前     : 覆われていない出現があり、別の正解の物も含む（「RU.RH.RH3.AT形過給機」「検査部門,検査部門長」）。
# ──────────────────────────────────────────────────────────────────
import re, collections
from common import norm, norm2 as _n2

TIERS = ["strict", "modified", "sentence", "compound"]

def norm2(s):
    return _n2(s).replace("ー", "-")

def _cls(ch):
    if ch.isascii() and ch.isalpha(): return "a"
    if ch.isdigit(): return "d"
    return "o"

def occurrences(key, name):
    out = []; i = name.find(key)
    while i >= 0:
        j = i + len(key); ok = True
        if i > 0:
            c0, p = _cls(key[0]), name[i-1]
            if c0 == "a" and _cls(p) == "a": ok = False
            if c0 == "d" and (_cls(p) == "d" or p == "."): ok = False
        if j < len(name):
            c1, q = _cls(key[-1]), name[j]
            if c1 == "a" and _cls(q) == "a": ok = False
            if c1 == "d" and _cls(q) == "d": ok = False
        if ok: out.append((i, j))
        i = name.find(key, i + 1)
    return out

def usable(key):
    if re.fullmatch(r"[A-Z0-9.\-/]+", key): return len(key) >= 2
    return len(key) >= 3

def is_serial(raw_name):
    return bool(re.match(r"^REQ-", raw_name or ""))

class Matcher:
    def __init__(self, gold_entities):
        self.keys = {ge["id"]: {norm2(ge["id"])} | {norm2(a) for a in ge.get("aliases", [])} for ge in gold_entities}
        ids = [norm2(ge["id"]) for ge in gold_entities]
        self.generic = {ge["id"] for ge in gold_entities if sum(1 for x in ids if norm2(ge["id"]) in x and x != norm2(ge["id"])) >= 3}
    def match_name(self, raw_name):
        """名前 1 つに対し {gold id: (段階, 被覆率)} を返す"""
        if is_serial(raw_name): return {}
        name = norm2(raw_name or "")
        if not name: return {}
        base = re.sub(r"[(\[][^()\[\]]*[)\]]$", "", name) or name      # 末尾の括弧書きを除いた名前
        occ = collections.defaultdict(list)
        for gid, ks in self.keys.items():
            for k in ks:
                if name == k or base == k: occ[gid].append((0, len(k), k)); continue
                if len(k) >= 3:      # いまの数え方と同じ幅（前後 3 文字までの伸び）。ここでは隣の文字の条件を付けない
                    for nm in (name, base):
                        if len(nm) - len(k) <= 3:
                            if nm.startswith(k): occ[gid].append((0, len(k), k))
                            elif nm.endswith(k): occ[gid].append((len(nm) - len(k), len(nm), k))
                if not usable(k): continue
                for (i, j) in occurrences(k, name): occ[gid].append((i, j, k))
        live = {}
        for gid, lst in occ.items():
            keep = [(i, j, k) for (i, j, k) in lst
                    if not any(g2 != gid and any((i2 <= i and j <= j2 and (j2 - i2) > (j - i)) for (i2, j2, _) in l2) for g2, l2 in occ.items())]
            if keep: live[gid] = keep
        res = {}
        for gid, keep in live.items():
            best = max(j - i for (i, j, _) in keep); cov = best / len(name)
            strict = any((nm == k) or (len(k) >= 3 and (nm.startswith(k) or nm.endswith(k)) and len(nm) - len(k) <= 3) for (_, _, k) in keep for nm in (name, base))
            others = [g2 for g2 in live if g2 != gid and g2 not in self.generic]
            if strict: tier = "strict"
            elif others: tier = "compound"
            elif cov >= 0.5: tier = "modified"
            else: tier = "sentence"
            res[gid] = (tier, round(cov, 2))
        return res
    def value_in(self, gid, text):
        """正解の値（gid）の鍵が、正規化した文字列 text に出現するか"""
        t = norm2(text or "")
        return any((t == k) or (len(k) >= 3 and occurrences(k, t)) for k in self.keys[gid])
