from zipfile import ZipFile, ZIP_DEFLATED
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path("CIS_FIN_Project_Overview.pptx")
W, H = 12192000, 6858000
NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

slides = [
    ("CIS-FIN", ["Evidence-grounded AI-readiness benchmarking", "Annual reports → TOE factors → comparable company scores", "Project overview · 2025–2026 pilot"]),
    ("The business problem", ["Annual reports contain valuable but unstructured AI-readiness evidence.", "Manual comparison is slow, inconsistent, and difficult to audit.", "CIS-FIN turns report evidence into traceable, industry-specific benchmarks."]),
    ("End-to-end workflow", ["1. Upload annual report PDF", "2. Extract selected pages and create stable chunks", "3. Generate grounded questions and embeddings", "4. Classify Technology / Organization / Environment", "5. Score evidence from 0–5", "6. Train provisional model and publish immutable benchmark versions"]),
    ("Architecture", ["Researcher app → PostgreSQL / object storage / Qdrant / local Ollama", "Client app → latest valid immutable benchmark → company comparison and evidence", "Source of truth: PostgreSQL metadata and benchmark artifacts; Qdrant stores vectors for retrieval"]),
    ("Data and processing layer", ["PostgreSQL: companies, documents, chunks, jobs, evidence, approvals, benchmark versions", "Object storage: original PDFs under deterministic company/checksum keys", "PyMuPDF extraction with page citations and bounded chunk batches", "Duplicate checksum detection and resumable processing jobs", "Qdrant: idempotent chunk embeddings and retrieval payloads"]),
    ("TOE framework and question discovery", ["Technology: infrastructure, data, models, applications, platforms", "Organization: people, leadership, governance, operating model", "Environment: regulation, partners, customers, market forces", "Similar evidence is grouped with embedding cosine similarity (current threshold: 0.78).", "A recurring theme requires majority support across distinct companies (minimum 2).", "Question generation is company-agnostic and validated against evidence and TOE dimension."]),
    ("Evidence scoring", ["Each question receives a 0–5 score from the same local Ollama rubric.", "0 = no evidence; 3 = established capability; 5 = leading, integrated capability.", "The model returns score, rationale, evidence quote, page, and confidence.", "Strict rescoring rejects malformed or failed model responses rather than mixing fallback scores.", "The client converts 0–5 to 0–100 for presentation."]),
    ("Benchmark versions and automation", ["A completed same-industry, same-year report is automatically enrolled.", "Existing questions are carried forward; new recurring themes are candidates.", "Strong candidates pass majority support, validation, and confidence gates.", "Each cohort update creates an immutable version; previous versions are preserved.", "Industry validation prevents non-IT companies from entering the IT cohort."]),
    ("Pilot regression model", ["Feature matrix: one row per company and one feature per benchmark question.", "Provisional target = average factor score × 20.", "Ridge regression produces pilot predictions and rankings.", "Pilot scores are directional—not production-grade truth—until reviewed targets are available.", "Full-cohort and individual strict rescoring are available from the dashboard."]),
    ("Client experience", ["Select industry and fiscal year.", "Review cumulative score and TOE dimension averages.", "Open any factor to compare every company in the cohort.", "Inspect supporting page ranges, chunk IDs, source text, and scoring rationale.", "The client reads benchmark data; it does not silently mutate the cohort."]),
    ("Controls, lessons, and current status", ["Duplicate questions are removed from future benchmark versions.", "Invalid industry membership is rejected before automatic enrollment.", "Malformed Ollama JSON and transient timeouts are retried; strict mode prevents silent fallbacks.", "Current validated IT cohort: HCLTECH, INFY, LTM, TCS, WIPRO.", "Next priorities: clean legacy question set, calibrate confidence, replace provisional targets, and broaden validation."]),
    ("Takeaway", ["CIS-FIN creates a traceable path from annual-report evidence to benchmark insight.", "The architecture separates ingestion, evidence retrieval, scoring, training, versioning, and client presentation.", "Automation reduces manual work while preserving reproducibility and auditability."]),
]

def xstr(tag, attrs, body=""):
    a = " ".join(f'{k}="{escape(str(v))}"' for k,v in attrs.items())
    return f"<{tag} {a}>{body}</{tag}>"

def run(text, size, bold=False, color="F4F7FB"):
    rpr = f'<a:rPr lang="en-US" sz="{size}"' + (' b="1"' if bold else '') + f'><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr>'
    return f'<a:r><a:rPr lang="en-US" sz="{size}"' + (' b="1"' if bold else '') + f'/><a:t>{escape(text)}</a:t></a:r>'

def textbox(text, x, y, w, h, size=2200, bold=False, color="F4F7FB"):
    paras = ''.join(f'<a:p><a:pPr marL="0" lvl="0"/><a:r><a:rPr lang="en-US" sz="{size}"' + (' b="1"' if bold else '') + f'/><a:t>{escape(line)}</a:t></a:r></a:p>' for line in text.split('\n'))
    return f'''<p:sp><p:nvSpPr><p:cNvPr id="{x}" name="TextBox {x}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>{paras}</p:txBody></p:sp>'''

def slide_xml(index, title, bullets):
    shapes = [textbox(title, 520000, 350000, 11000000, 650000, 3000, True, "FFFFFF")]
    if index == 4:
        boxes = [(700000,1600000,2200000,750000,"Researcher\nStreamlit"),(3300000,1600000,2200000,750000,"PostgreSQL\nmetadata + audit"),(5900000,1600000,2200000,750000,"Object storage\noriginal PDFs"),(8500000,1600000,2200000,750000,"Qdrant\nembeddings"),(3300000,3300000,2200000,750000,"Local Ollama\nquestions + scoring"),(5900000,3300000,2200000,750000,"Benchmark engine\nTOE + matrix + regression"),(8500000,3300000,2200000,750000,"Client app\ncomparison + evidence")]
        for i,(x,y,w,h,t) in enumerate(boxes):
            shapes.append(textbox(t,x,y,w,h,1900,True,"D7E9FF"))
        shapes.append(textbox("PDF → pages → chunks → embeddings → TOE themes → scores → immutable version → client", 900000, 5200000, 10000000, 500000, 1900, True, "8FD3C7"))
    else:
        y = 1400000
        for bullet in bullets:
            shapes.append(textbox("• " + bullet, 900000, y, 10300000, 520000, 2050, False, "E5ECF4")); y += 700000
    bg = '<p:sp><p:nvSpPr><p:cNvPr id="1" name="Background"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:solidFill><a:srgbClr val="101820"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>'
    body = bg + ''.join(shapes)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="{NS}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="0" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>{body}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'''

def content_types(n):
    items = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>','<Default Extension="xml" ContentType="application/xml"/>','<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>','<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>','<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>','<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>']
    for i in range(1,n+1): items.append(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    return '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'+''.join(items)+'</Types>'

def rels(n):
    return '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>'

def presentation(n):
    ids=''.join(f'<p:sldId id="{255+i}" r:id="rId{i+1}"/>' for i in range(n))
    return f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:a="{NS}" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId{n+1}"/></p:sldMasterIdLst><p:sldIdLst>{ids}</p:sldIdLst><p:sldSz cx="{W}" cy="{H}" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'

def pres_rels(n):
    return '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i+1}.xml"/>' for i in range(n))+f'<Relationship Id="rId{n+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/></Relationships>'

THEME = '<?xml version="1.0" encoding="UTF-8"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="CIS-FIN"><a:themeElements><a:clrScheme name="CIS-FIN"><a:dk1><a:srgbClr val="101820"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="203040"/></a:dk2><a:lt2><a:srgbClr val="E5ECF4"/></a:lt2><a:accent1><a:srgbClr val="4F9DFF"/></a:accent1><a:accent2><a:srgbClr val="8FD3C7"/></a:accent2><a:accent3><a:srgbClr val="F2B134"/></a:accent3><a:accent4><a:srgbClr val="E06C75"/></a:accent4><a:accent5><a:srgbClr val="B48EAD"/></a:accent5><a:accent6><a:srgbClr val="98C379"/></a:accent6><a:hlink><a:srgbClr val="4F9DFF"/></a:hlink><a:folHlink><a:srgbClr val="B48EAD"/></a:folHlink></a:clrScheme><a:fontScheme name="CIS-FIN"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme><a:fmtScheme name="CIS-FIN"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'

MASTER = '<?xml version="1.0" encoding="UTF-8"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr></p:spTree></p:cSld><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles/></p:sldMaster>'
LAYOUT = '<?xml version="1.0" encoding="UTF-8"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr></p:spTree></p:cSld></p:sldLayout>'

with ZipFile(OUT, "w", ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", content_types(len(slides)))
    z.writestr("_rels/.rels", rels(len(slides)))
    z.writestr("ppt/presentation.xml", presentation(len(slides)))
    z.writestr("ppt/_rels/presentation.xml.rels", pres_rels(len(slides)))
    z.writestr("ppt/theme/theme1.xml", THEME)
    z.writestr("ppt/slideMasters/slideMaster1.xml", MASTER)
    z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>')
    z.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT)
    z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>')
    for i,(title, bullets) in enumerate(slides,1):
        z.writestr(f"ppt/slides/slide{i}.xml", slide_xml(i,title,bullets))
        z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>')
print(OUT)
