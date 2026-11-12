import pypdfium2 as pdfium, pypdfium2.raw as raw
for f,pg in [("/in/full56/HS-RU-003H.pdf",0),("/in/full56/HS-TP発行目録_H23.9.27.pdf",0),("/in/full56/VTR-T005B.pdf",5)]:
    pdf=pdfium.PdfDocument(f); page=pdf[pg]; print(f, page.get_size())
    for obj in page.get_objects(max_depth=15):
        if obj.type==raw.FPDF_PAGEOBJ_IMAGE:
            print(' lvl',obj.level,'pos',[round(x,1) for x in obj.get_bounds()],'px',obj.get_px_size(),'mat',obj.get_matrix())
        elif obj.type==raw.FPDF_PAGEOBJ_FORM:
            print(' FORM lvl',obj.level,[round(x,1) for x in obj.get_bounds()])
