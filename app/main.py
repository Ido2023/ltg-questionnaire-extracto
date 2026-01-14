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

QUESTION_START_RE = re.compile(r"^\s*(\d{1,3})[\.\)]\s+(.*)")
ANSWER_BULLET_RE = re.compile(r"^\s*[\u2022\-–•]\s+(.*)")
ANSWER_LETTER_RE = re.compile(r"^\s*[א-ת]\)\s+(.*)")

QUESTION_KEYWORDS = [
    "מי", "מה", "איזו", "איזה", "עד כמה", "האם", "כמה", "לאיזה", "למי"
]

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def looks_like_real_question(text: str) -> bool:
    if "?" in text:
        return True
    if text.endswith(":"):
        return True
    for w in QUESTION_KEYWORDS:
        if w in text:
            return True
    return False

def is_question_start(line: str) -> Optional[str]:
    m = QUESTION_START_RE.match(line)
    if not m:
        return None

    candidate = clean_text(m.group(2))
    if looks_like_real_question(candidate):
        return candidate

    return None  # ← מספר שלא נראה שאלה

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
# DOCX parsing
# -----------------------------
def parse_docx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    lines = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]

    questions = []
    current_q = None

    for line in lines:
        q_text = is_question_start(line)

        if q_text:
            if current_q:
                current_q["type"] = infer_question_type(
                    current_q["text"], current_q["answers"]
                )
                questions.append(current_q)

            current_q = {
                "text": q_text,
                "answers": [],
                "context": []
            }
            continue

        if not current_q:
            continue

        if is_answer_line(line):
            current_q["answers"].append(strip_answer_prefix(line))
        else:
            current_q["context"].append(line)

    if current_q:
        current_q["type"] = infer_question_type(
            current_q["text"], current_q["answers"]
        )
        questions.append(current_q)

    output = []
    for i, q in enumerate(questions, start=1):
        output.append({
            "text": q["text"],
            "type": q["type"],
            "answers": q["answers"],
            "meta": {
                "question_index": i,
                "context": " ".join(q["context"]) if q["context"] else None,
                "source": "docx"
            }
        })

    return output

# -----------------------------
# API
# -----------------------------
@app.post("/extract")
async def extract_questions(file: UploadFile = File(...)):
    raw = await file.read()
    filename = file.filename or ""

    try:
        if filename.endswith(".docx"):
            questions = parse_docx_questions(raw)
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
