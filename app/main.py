from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import re
import io
from typing import List, Dict, Any, Optional

app = FastAPI()

# -----------------------------
# CORS
# -----------------------------
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

QUESTION_NUMBER_RE = re.compile(r"^\s*(\d{1,3})[\.\)]\s+(.*)")
ANSWER_BULLET_RE = re.compile(r"^\s*[\u2022\-–•]\s+(.*)")
ANSWER_LETTER_RE = re.compile(r"^\s*[א-ת]\)\s+(.*)")

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def is_question_start(line: str) -> Optional[re.Match]:
    return QUESTION_NUMBER_RE.match(line)

def is_answer_line(line: str) -> bool:
    return bool(
        ANSWER_BULLET_RE.match(line)
        or ANSWER_LETTER_RE.match(line)
    )

def strip_answer_prefix(line: str) -> str:
    line = ANSWER_BULLET_RE.sub(r"\1", line)
    line = ANSWER_LETTER_RE.sub(r"\1", line)
    return clean_text(line)

def infer_question_type(question_text: str, answers: List[str]) -> str:
    if not answers:
        return "open"
    if any(x in question_text for x in ["בחר", "סמן", "אפשר לבחור", "יותר מתשובה אחת"]):
        return "multi_choice"
    return "single_choice"

# -----------------------------
# DOCX parsing (ROBUST)
# -----------------------------
def parse_docx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        from docx import Document
    except Exception:
        raise RuntimeError("python-docx is missing")

    doc = Document(io.BytesIO(file_bytes))
    lines = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]

    questions: List[Dict[str, Any]] = []
    current_q: Optional[Dict[str, Any]] = None

    for line in lines:
        q_match = is_question_start(line)

        # -------------------------
        # NEW QUESTION
        # -------------------------
        if q_match:
            if current_q:
                current_q["type"] = infer_question_type(
                    current_q["text"], current_q["answers"]
                )
                questions.append(current_q)

            current_q = {
                "text": clean_text(q_match.group(2)),
                "answers": [],
                "context": []
            }
            continue

        if not current_q:
            continue

        # -------------------------
        # ANSWER
        # -------------------------
        if is_answer_line(line):
            current_q["answers"].append(strip_answer_prefix(line))
            continue

        # -------------------------
        # CONTEXT (long text, explanation)
        # -------------------------
        current_q["context"].append(line)

    # flush last question
    if current_q:
        current_q["type"] = infer_question_type(
            current_q["text"], current_q["answers"]
        )
        questions.append(current_q)

    # finalize
    output = []
    for idx, q in enumerate(questions, start=1):
        output.append({
            "text": q["text"],
            "type": q["type"],
            "answers": q["answers"],
            "meta": {
                "question_index": idx,
                "context": " ".join(q["context"]) if q["context"] else None,
                "source": "docx"
            }
        })

    return output

# -----------------------------
# CSV parsing
# -----------------------------
def parse_csv_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    import csv
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    out = []
    for row in reader:
        q = clean_text(row.get("question") or row.get("שאלה") or "")
        if not q:
            continue

        answers_raw = row.get("answers") or row.get("תשובות") or ""
        answers = [clean_text(x) for x in re.split(r"[;\|]", answers_raw) if clean_text(x)]

        out.append({
            "text": q,
            "type": infer_question_type(q, answers),
            "answers": answers,
            "meta": {
                "question_index": len(out) + 1,
                "source": "csv"
            }
        })
    return out

# -----------------------------
# XLSX parsing
# -----------------------------
def parse_xlsx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        import openpyxl
    except Exception:
        raise RuntimeError("openpyxl is missing")

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
            "text": q,
            "type": infer_question_type(q, answers),
            "answers": answers,
            "meta": {
                "question_index": len(out) + 1,
                "source": "xlsx"
            }
        })

    return out

# -----------------------------
# API
# -----------------------------
@app.post("/extract")
async def extract_questions(file: UploadFile = File(...)):
    raw = await file.read()
    filename = file.filename or ""
    content_type = file.content_type or ""

    try:
        if filename.endswith(".docx"):
            questions = parse_docx_questions(raw)
        elif filename.endswith(".csv"):
            questions = parse_csv_questions(raw)
        elif filename.endswith(".xlsx"):
            questions = parse_xlsx_questions(raw)
        else:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Unsupported file type"}
            )

        return JSONResponse({
            "status": "parsed",
            "filename": filename,
            "questions_count": len(questions),
            "questions": questions
        })

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )
