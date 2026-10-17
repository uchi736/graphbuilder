import sys, fitz
def count(p):
    doc=fitz.open(p); out=[]
    for page in doc:
        A=page.rect.get_area(); tabs=page.find_tables().tables; out.append(sum(1 for t in tabs if fitz.Rect(t.bbox).get_area()/A>=0.7))
    doc.close(); return out
p="/docs/試運転要領/HS-VTR-T001E.pdf"
print("pymupdf4llm なし:", count(p))
import pymupdf4llm
print("pymupdf4llm を import した後:", count(p))
