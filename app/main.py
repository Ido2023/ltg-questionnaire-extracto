from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import re
import io
from typing import List, Dict, Any, Optional

app = FastAPI()

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Healthcheck
# -----------------------------
@app.get("/")
def root():
    return {"status": "ok", "service": "LTG Questionnaire Extractor"}

# -----------------------------
# Helpers
# -----------------------------

QUESTION_PATTERNS = [
    r"^\s*\d+[\)\.\-]\s+",
    r"^\s*[\u2022\-\*]\s+",
    r"^\s*[א-ת]\)\s+",
]

ANSWER_PREFIX_PATTERNS = [
    r"^\s*[\u2022\-\*]\s+",
    r"^\s*\d+[\)\.\-]\s+",
    r"^\s*[א-ת]\)\s+",
]

MULTI_CHOICE_HINTS = [
    "אפשר לבחור", "יותר מתשובה אחת", "מספר תשובות",
    "בחר/י עד", "בחרו עד", "סמן/י את כל"
]

QUESTION_START_WORDS = [
    "איזה", "מה", "עד כמה", "באיזו מידה", "האם",
    "מי", "כיצד", "כמה", "למי", "לאיזו מידה"
]

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def looks_like_question(line: str) -> bool:
    t = clean_text(line)
    if len(t) < 3:
        return False
    if t.endswith("?") or re.search(r":\s*$", t):
        return True
    for p in QUESTION_PATTERNS:
        if re.search(p, t) and len(t) >= 8:
            return True
    if re.search(r"\b(עד כמה|באיזו מידה|מה דעתך|למי תצביע|האם|כמה)\b", t):
        return True
    return False

def strip_prefixes(s: str) -> str:
    t = s
    for p in QUESTION_PATTERNS + ANSWER_PREFIX_PATTERNS:
        t = re.sub(p, "", t)
    return clean_text(t)

def looks_like_answer(line: str) -> bool:
    t = clean_text(line)
    if len(t) < 1:
        return False
    for p in ANSWER_PREFIX_PATTERNS:
        if re.search(p, t):
            return True
    if len(t) <= 40 and not looks_like_question(t):
        return True
    return False

def infer_question_type(question_text: str, answers: List[str]) -> str:
    qt = clean_text(question_text)
    if not answers:
        return "open"
    for hint in MULTI_CHOICE_HINTS:
        if hint in qt:
            return "multi_choice"
    return "single_choice"

# -----------------------------
# NEW: Context / Question split
# -----------------------------
def split_context_and_question(text: str) -> Dict[str, str]:
    if not text:
        return {"context": "", "question": ""}

    text = clean_text(text)

    if "?" in text:
        parts = re.split(r'(?<=\?)', text)
        return {
            "context": clean_text(" ".join(parts[:-1])),
            "question": clean_text(parts[-1])
        }

    for w in QUESTION_START_WORDS:
        idx = text.find(w)
        if idx > 40:
            return {
                "context": clean_text(text[:idx]),
                "question": clean_text(text[idx:])
            }

    if len(text) > 180:
        return {"context": text, "question": ""}

    return {"context": "", "question": text}

# -----------------------------
# DOCX parsing
# -----------------------------
def parse_docx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]

    questions: List[Dict[str, Any]] = []
    current_q: Optional[Dict[str, Any]] = None

    def flush_current():
        nonlocal current_q
        if not current_q:
            return

        answers_clean = []
        seen = set()
        for a in current_q.get("answers", []):
            aa = strip_prefixes(a)
            if aa and aa not in seen:
                seen.add(aa)
                answers_clean.append(aa)

        raw_text = strip_prefixes(current_q.get("text", ""))
        split = split_context_and_question(raw_text)

        qtype = infer_question_type(split["question"], answers_clean)

        questions.append({
            "context": split["context"],
            "text": split["question"],
            "type": qtype,
            "answers": answers_clean,
            "meta": {
                "source": "docx",
                "question_index": len(questions) + 1
            }
        })
        current_q = None

    i = 0
    while i < len(paragraphs):
        line = paragraphs[i]

        if looks_like_question(line):
            flush_current()
            current_q = {"text": line, "answers": []}
            i += 1
            while i < len(paragraphs):
                nxt = paragraphs[i]
                if looks_like_question(nxt):
                    break
                if looks_like_answer(nxt):
                    current_q["answers"].append(nxt)
                else:
                    if current_q and not current_q["answers"]:
                        current_q["text"] += " " + nxt
                i += 1
            continue
        i += 1

    flush_current()
    return questions

# -----------------------------
# CSV / XLSX
# -----------------------------
def parse_csv_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    import csv
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        q = clean_text(row.get("question") or row.get("Question") or row.get("שאלה") or "")
        if not q:
            continue
        answers_raw = row.get("answers") or row.get("Answers") or row.get("תשובות") or ""
        answers = [clean_text(x) for x in re.split(r"[;\|]", answers_raw) if clean_text(x)]
        out.append({
            "context": "",
            "text": q,
            "type": infer_question_type(q, answers),
            "answers": answers,
            "meta": {"source": "csv", "question_index": len(out) + 1}
        })
    return out

def parse_xlsx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    headers = [clean_text(str(c.value)) if c.value else "" for c in ws[1]]
    idx_q = headers.index("שאלה") if "שאלה" in headers else None
    idx_a = headers.index("תשובות") if "תשובות" in headers else None

    if idx_q is None:
        return []

    out = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        q = clean_text(str(r[idx_q])) if r[idx_q] else ""
        if not q:
            continue
        answers = []
        if idx_a is not None and r[idx_a]:
            answers = [clean_text(x) for x in re.split(r"[;\|]", str(r[idx_a])) if clean_text(x)]
        out.append({
            "context": "",
            "text": q,
            "type": infer_question_type(q, answers),
            "answers": answers,
            "meta": {"source": "xlsx", "question_index": len(out) + 1}
        })
    return out

# -----------------------------
# API endpoint
# -----------------------------
@app.post("/extract")
async def extract_questions(file: UploadFile = File(...)):
    raw = await file.read()
    filename = file.filename or "uploaded"
    content_type = file.content_type or ""

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        if ext == "docx":
            questions = parse_docx_questions(raw)
        elif ext == "csv":
            questions = parse_csv_questions(raw)
        elif ext in ["xlsx", "xlsm", "xltx", "xltm"]:
            questions = parse_xlsx_questions(raw)
        else:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Unsupported file type"})

        return JSONResponse({
            "status": "parsed",
            "filename": filename,
            "questions_count": len(questions),
            "questions": questions
        })

    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
