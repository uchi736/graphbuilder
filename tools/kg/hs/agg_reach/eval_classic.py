# 古典的な名寄せの手法を、ラベル付きの 268 組で比べる
import json,re,unicodedata,collections,numpy as np
P=json.load(open('pairs_sample.json')); lab={}
for l in open('labels_claude_20261113.txt'):
    if l.strip() and not l.startswith('#'): n,v=l.split(); lab[int(n)]=v
assert len(lab)==len(P),(len(lab),len(P))
for n,r in enumerate(P): r['y']=lab[n]
print('組',len(P),collections.Counter(r['y'] for r in P), collections.Counter((r['t'],r['y']) for r in P))
def nk(s): return re.sub(r'[\s_\-‐‑‒–—―−]+','',unicodedata.normalize('NFKC',s or '')).casefold()
# 飾りを外した核（規則）: 規格番号の前置き・「相当材」「材」・検査/試験などの語尾・括弧の中
PRE=re.compile(r'^(jis[a-z]\d{4}(\(\d{4}\))?|din\d+の?|特殊耐熱合金|ニレジスト|nire|\d項[\d.]*\d?\)?)')
SUF=re.compile(r'(又は相当材|または相当材|相当材|材|検査|試験|観察|測定|計測|試験片|号試験片|号)$')
def core(s):
    k=nk(s); k=re.sub(r'\(.*?\)','',k); k=re.sub(r'[、,・/]','',k)
    for _ in range(3):
        k2=SUF.sub('',PRE.sub('',k)); 
        if k2==k or not k2: break
        k=k2
    return k
for r in P: r['core']=int(core(r['a'])==core(r['b']))
def show(name,f):
    sel=[r for r in P if f(r)]; c=collections.Counter(r['y'] for r in sel); n=len(sel) or 1
    S=sum(r['y']=='S' for r in P); SB=sum(r['y']!='D' for r in P)
    print(f"{name:<26} 該当 {len(sel):>3} | 同じ物 {c['S']:>3} 広い狭い {c['B']:>3} 別物 {c['D']:>3} | 同じ物の適合率 {c['S']/n:.2f} 再現率 {c['S']/S:.2f} | 関連(同じ+広い狭い)の適合率 {(c['S']+c['B'])/n:.2f} 再現率 {(c['S']+c['B'])/SB:.2f}")
print('\n== 単独の手法')
show('編集距離の類似 ≥0.8',lambda r:r['lev']>=0.8); show('編集距離の類似 ≥0.6',lambda r:r['lev']>=0.6)
show('Jaro-Winkler ≥0.9',lambda r:r['jw']>=0.9); show('Jaro-Winkler ≥0.8',lambda r:r['jw']>=0.8)
show('文字2-gram Dice ≥0.6',lambda r:r['dice']>=0.6); show('文字2-gram Dice ≥0.4',lambda r:r['dice']>=0.4)
show('部分一致(包含)',lambda r:r['contain']); show('符号が一致',lambda r:r['code']=='same'); show('符号が不一致',lambda r:r['code']=='diff')
show('飾りを外して一致',lambda r:r['core']); show('埋め込み ≥0.95',lambda r:r['emb']>=0.95); show('埋め込み 近い順5個',lambda r:r['rank']<=5)
print('\n== 規則の組合せ')
same=lambda r:(r['core'] or r['code']=='same') and r['code']!='diff'
show('同じ物: 核一致か符号一致',same)
show('広い狭い: 包含(上以外)',lambda r:r['contain'] and not same(r) and r['code']!='diff')
show('どちらか',lambda r:(same(r) or r['contain']) and r['code']!='diff')
print('  規則で同じ物に入らなかった同じ物:',[(r['a'],r['b']) for r in P if r['y']=='S' and not same(r)])
print('  規則で同じ物に入った別物・広い狭い:',[(r['a'],r['b'],r['y']) for r in P if r['y']!='S' and same(r)])
# 特徴量からの学習（ロジスティック回帰・勾配ブースティング、5 分割の交差検証）
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold,cross_val_predict
from sklearn.metrics import precision_recall_fscore_support as prf
def X(cols): return np.array([[ {'same':1,'diff':-1,'none':0}[r[c]] if c=='code' else r[c] for c in cols] for r in P],dtype=float)
y=np.array([r['y']=='S' for r in P]); skf=StratifiedKFold(5,shuffle=True,random_state=0)
print('\n== 特徴量から学習（同じ物かどうか、5 分割交差検証）')
for name,cols in [('文字列だけ',['lev','jw','dice','contain','lcs','code','core']),('文字列＋埋め込み',['lev','jw','dice','contain','lcs','code','core','emb']),('埋め込みだけ',['emb'])]:
    for mname,m in [('ロジスティック回帰',LogisticRegression(max_iter=1000,class_weight='balanced')),('勾配ブースティング',GradientBoostingClassifier(random_state=0))]:
        p=cross_val_predict(m,X(cols),y,cv=skf); pr,rc,f,_=prf(y,p,average='binary'); print(f'  {name:<10} {mname:<10} 適合率 {pr:.2f} 再現率 {rc:.2f} F {f:.2f}')
json.dump(P,open('pairs_labeled.json','w'),ensure_ascii=False)
