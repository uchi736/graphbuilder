import json,re,sys,unicodedata,collections
S=sys.argv[1]
import base64
rows=[json.loads(base64.b64decode(ln.strip().strip('"'))) for ln in open(S+'/chunks_kgb6_20261112.b64',encoding='utf-8').read().split('\n')[1:] if ln.strip()]
def nk(s): return re.sub(r'[\s_\-‐‑‒–—―−]+','',unicodedata.normalize('NFKC',s or '')).casefold()
def dk(s): return nk(s).replace('本文','').replace('生','')
ALLDOCS={dk(r['doc']) for r in rows}
qs=[q for q in json.load(open('/home/uchida/ai-stack/llm-graph-builder/tools/kg/hs/qa/questions_hs.json',encoding='utf-8'))['questions'] if q['type']=='agg']
TERMS={'HS-10':(['sacm645'],r'sacm645'),'HS-11':(['scm440h'],r'scm440h'),'HS-15':(['クロムカーバイド','cr3c2'],r'クロムカーバイド|cr3c2'),
'HS-16':(['y型b号','jisg5502y型b号'],r'y型b号'),'HS-17':(['jisz22014号試験片','jisz22014号','4号試験片'],r'jisz2201'),'HS-18':(['fcd400'],r'fcd400'),
'HS-19':(['窒化','窒化処理'],r'窒化'),'HS-20':(['qckai027','初品管理規定','初品管理規程'],r'qckai027|初品管理規[定程]'),'HS-23':(['蛍光浸透探傷','蛍光浸透探傷検査','蛍光浸透探傷試験','ザイクロ検査','ザイクロ'],r'蛍光浸透|ザイクロ')}
out=[]
for q in qs:
    exact,pat=TERMS[q['qid']]; pat=re.compile(pat)
    gold={dk(e['doc']) for e in q['evidence']}; miss=gold-ALLDOCS
    if miss: print('  !! グラフに無い文書名',miss)
    A=collections.defaultdict(set);B=collections.defaultdict(set);C=collections.defaultdict(set);names=collections.Counter()
    for r in rows:
        d=dk(r['doc'])
        for e in r['ents']:
            if not e.get('id'): continue
            k=nk(e['id'])
            if k in exact: A[d].add(r['chunk'])
            if pat.search(k): B[d].add(r['chunk']); names[e['id']+'〔'+'/'.join(e['labels'] or [])+'〕']+=1
        if pat.search(nk(r['text'])): C[d].add(r['chunk'])
    f=lambda M:(len(gold&set(M)),len(set(M)-gold),sum(len(v) for v in M.values()))
    out.append((q['qid'],len(gold),f(A),f(B),f(C),len(names),sorted(gold-set(B)),sorted(gold-set(C))))
    print(q['qid'],'正解の文書',len(gold),'| A 今のまま(名前一致) 届く/余分/チャンク',f(A),'| B 名寄せ上限(名前に含む)',f(B),'| C 本文に語がある',f(C),'| 該当エンティティの種類数',len(names))
    print('    B で届かない:',sorted(gold-set(B)),' C でも届かない:',sorted(gold-set(C)))
    print('    名前の例:',[n for n,_ in names.most_common(8)])
tot=lambda i:(sum(o[i][0] for o in out),sum(o[1] for o in out))
print('合計 届いた正解の文書 A',tot(2),'B',tot(3),'C',tot(4))
print('全部届いた問い A',sum(o[2][0]==o[1] for o in out),'B',sum(o[3][0]==o[1] for o in out),'C',sum(o[4][0]==o[1] for o in out),'/',len(out))
