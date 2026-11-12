# 埋め込みの近さで「同じ物の組」がどこに出るかを見る（読み取りだけ）
import json,base64,re,unicodedata,collections,numpy as np
E=[json.loads(base64.b64decode(l.strip().strip('"'))) for l in open('ent_emb_kgb6_20261112.b64').read().split('\n')[1:] if l.strip()]
def nk(s): return re.sub(r'[\s_\-‐‑‒–—―−]+','',unicodedata.normalize('NFKC',s or '')).casefold()
names=[e['id'] for e in E]; idx={n:i for i,n in enumerate(names)}
M=np.array([e['emb'] for e in E],dtype=np.float32); M/=np.linalg.norm(M,axis=1,keepdims=True); S=M@M.T
print('対象',len(E))
KNOWN=[('SACM645','SACM645相当材'),('SACM645','JIS G 4202 SACM645 材'),('SACM645相当材','SACM 645 相当材'),('SCM440H','SCM440H相当材'),('FCD400','FCD400材'),
 ('ザイクロ検査','蛍光浸透探傷検査'),('蛍光浸透探傷検査','蛍光浸透探傷試験'),('蛍光浸透探傷検査','蛍光探傷(ザイクロ)検査'),('蛍光浸透探傷検査','蛍光浸透探傷検査(ザイクロ検査)'),
 ('Ni・Cr基のCr3C2(クロムカーバイト)','Ni・Cr基のCr3C2(クロムカーバイド)'),('Ni・Cr基のCr3C2(クロムカーバイト)','NiCr基材Cr3C2(クローム カーバイド)'),
 ('JIS Z 2201 4号試験片','JIS Z-2201 4号試験片'),('別鋳込み材 JIS G 5502 Y型 B号','別鋳込み材 JIS G 5502 Y型B号'),('QC-KAI-027','QC-KAI-027初品管理規定'),('QC-KAI-027','初品管理規程(QC-KAI-027)')]
DIFF=[('蛍光浸透探傷検査','染色浸透探傷検査'),('蛍光浸透探傷検査','超音波探傷検査'),('SACM645','SNCM630'),('SCM440H','SCM435H'),('FCD400','FCD450'),('JIS Z 2201 4号試験片','JIS Z-2201 8号B試験片'),('AC4A','AC4C'),('JIS Z 2201','JIS Z 2202'),('引張試験','硬さ試験'),('焼入れ','焼もどし')]
def rank(a,b):
    i,j=idx[a],idx[b]; order=np.argsort(-S[i]); return int(np.where(order==j)[0][0])
print('\n同じ物の組  近さ / a から見た b の順位(0=自分)')
for a,b in KNOWN:
    if a in idx and b in idx: print(f'  {S[idx[a],idx[b]]:.3f}  順位 {rank(a,b):>3}  {a} ／ {b}')
    else: print('  (無い)',a if a not in idx else b)
print('\n別物の組')
for a,b in DIFF:
    if a in idx and b in idx: print(f'  {S[idx[a],idx[b]]:.3f}  順位 {rank(a,b):>3}  {a} ／ {b}')
    else: print('  (無い)',a if a not in idx else b)
# しきい値ごとの候補の組の数（共通の種類があり、正規形が違う組）
lab=[set(e['labels']) for e in E]; keys=[nk(n) for n in names]
iu=np.triu_indices(len(E),1); sims=S[iu]
ok=np.array([bool(lab[i]&lab[j]) and keys[i]!=keys[j] for i,j in zip(*iu)])
print('\nしきい値ごとの候補の組(同じ種類・正規形が違う)')
for t in (0.97,0.95,0.93,0.90,0.87,0.85,0.80): print(f'  {t:.2f} 以上: {int(((sims>=t)&ok).sum())} 組')
import random; random.seed(1)
for lo,hi in ((0.95,1.01),(0.90,0.95),(0.85,0.90),(0.80,0.85)):
    sel=[k for k in np.where((sims>=lo)&(sims<hi)&ok)[0]]; random.shuffle(sel)
    print(f'\n[{lo:.2f}〜{hi:.2f}] {len(sel)} 組から 10 組')
    for k in sel[:10]: print(f'   {sims[k]:.3f}  {names[iu[0][k]]} ／ {names[iu[1][k]]}')
# 上位 K に入れる方式: 各エンティティの近い順 K 個
for K in (5,10,15):
    hit=sum(1 for a,b in KNOWN if a in idx and b in idx and (rank(a,b)<=K or rank(b,a)<=K)); tot=sum(1 for a,b in KNOWN if a in idx and b in idx)
    print(f'近い順 {K} 個に入る同じ物の組 {hit}/{tot}')
