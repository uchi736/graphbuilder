# 名寄せの古典的な手法の特徴量を、同じ種類の全ての組について計算する（読み取りだけ）
import json,base64,re,unicodedata,random,sys,numpy as np
TYPES=['Material','Specimen','TestItem']
E=[json.loads(base64.b64decode(l.strip().strip('"'))) for l in open('ent_emb_kgb6_20261112.b64').read().split('\n')[1:] if l.strip()]
E=[e for e in E if set(e['labels'])&set(TYPES)]
def nk(s): return re.sub(r'[\s_\-‐‑‒–—―−]+','',unicodedata.normalize('NFKC',s or '')).casefold()
def lev(a,b):
    if a==b: return 0
    p=list(range(len(b)+1))
    for i,ca in enumerate(a,1):
        c=[i]
        for j,cb in enumerate(b,1): c.append(min(p[j]+1,c[j-1]+1,p[j-1]+(ca!=cb)))
        p=c
    return p[-1]
def jaro(a,b):
    if a==b: return 1.0
    la,lb=len(a),len(b); w=max(la,lb)//2-1; ma=[0]*la; mb=[0]*lb; m=0
    for i in range(la):
        for j in range(max(0,i-w),min(lb,i+w+1)):
            if not mb[j] and a[i]==b[j]: ma[i]=mb[j]=1; m+=1; break
    if not m: return 0.0
    t=0;k=0
    for i in range(la):
        if ma[i]:
            while not mb[k]: k+=1
            if a[i]!=b[k]: t+=1
            k+=1
    return (m/la+m/lb+(m-t/2)/m)/3
def jw(a,b):
    j=jaro(a,b); l=0
    for x,y in zip(a[:4],b[:4]):
        if x!=y: break
        l+=1
    return j+l*0.1*(1-j)
def grams(s): return {s[i:i+2] for i in range(len(s)-1)} or {s}
def lcs(a,b):
    best=0; p=[0]*(len(b)+1)
    for ca in a:
        c=[0]
        for j,cb in enumerate(b,1):
            v=p[j-1]+1 if ca==cb else 0; c.append(v); best=max(best,v)
        p=c
    return best
CODE=re.compile(r'[a-z]*\d[a-z0-9.]*')
def codes(k): return frozenset(c for c in CODE.findall(k) if len(c)>=2)
names=[e['id'] for e in E]; K=[nk(n) for n in names]; G=[grams(k) for k in K]; C=[codes(k) for k in K]; L=[set(e['labels'])&set(TYPES) for e in E]
M=np.array([e['emb'] for e in E],dtype=np.float32); M/=np.linalg.norm(M,axis=1,keepdims=True); S=M@M.T
R=np.argsort(np.argsort(-S,axis=1),axis=1)
rows=[]
for i in range(len(E)):
    for j in range(i+1,len(E)):
        if not (L[i]&L[j]) or K[i]==K[j]: continue
        a,b=K[i],K[j]; sh,lo=(a,b) if len(a)<=len(b) else (b,a)
        rows.append(dict(i=i,j=j,a=names[i],b=names[j],t='/'.join(sorted(L[i]&L[j])),
            lev=round(1-lev(a,b)/max(len(a),len(b)),3),jw=round(jw(a,b),3),dice=round(2*len(G[i]&G[j])/(len(G[i])+len(G[j])),3),
            contain=int(len(sh)>=3 and sh in lo),lcs=round(lcs(sh,lo)/len(sh),3),
            code=('same' if C[i] and C[i]==C[j] else 'diff' if C[i] and C[j] and C[i]!=C[j] else 'none'),
            emb=round(float(S[i,j]),3),rank=int(min(R[i,j],R[j,i]))))
json.dump(rows,open('pairs_feat.json','w'),ensure_ascii=False)
print('エンティティ',len(E),'組',len(rows))
random.seed(7); pick={}
def take(name,sel,n):
    sel=[r for r in sel]; random.shuffle(sel)
    for r in sel[:n]: pick.setdefault((r['i'],r['j']),r)
    print(name,len(sel))
take('lev>=0.8',[r for r in rows if r['lev']>=0.8],30); take('lev 0.6-0.8',[r for r in rows if 0.6<=r['lev']<0.8],30)
take('jw>=0.9',[r for r in rows if r['jw']>=0.9],25); take('jw 0.8-0.9',[r for r in rows if 0.8<=r['jw']<0.9],25)
take('dice>=0.6',[r for r in rows if r['dice']>=0.6],25); take('dice 0.4-0.6',[r for r in rows if 0.4<=r['dice']<0.6],25)
take('contain',[r for r in rows if r['contain']],40); take('lcs>=0.8 非包含',[r for r in rows if r['lcs']>=0.8 and not r['contain']],25)
take('code same',[r for r in rows if r['code']=='same'],25)
take('emb rank<=5',[r for r in rows if r['rank']<=5],40); take('emb>=0.95',[r for r in rows if r['emb']>=0.95],20)
P=sorted(pick.values(),key=lambda r:(r['t'],r['a'],r['b']))
json.dump(P,open('pairs_sample.json','w'),ensure_ascii=False)
for n,r in enumerate(P): print(f"{n}\t{r['t']}\t{r['a']}\t|\t{r['b']}")
