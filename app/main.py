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

QUESTION_RE = re.compile(r"^\s*(\d+)\.\s+")
SCALE_ANSWER_RE = re.compile(r"^\s*\d+\s*[–\-]\s+")
BULLET_RE = re.compile(r"^\s*[•\-*]\s+")

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def strip_prefixes(s: str) -> str:
    s = re.sub(QUESTION_RE, "", s)
    s = re.sub(SCALE_ANSWER_RE, "", s)
    s = re.sub(BULLET_RE, "", s)
    return clean_text(s)

def looks_like_question(line: str) -> bool:
    t = clean_text(line)

    # חייב להתחיל במספר + נקודה
    if not QUESTION_RE.match(t):
        return False

    # קצר מדי = לא שאלה (למשל "1 - כלל לא")
    if len(t) < 20:
        return False

    # ניסוחי שאלה
    if "?" in t:
        return True

    if re.search(r"\b(מי|מה|עד כמה|באיזו מידה|איזה|לאיזה|האם)\b", t):
        return True

    # fallback – שאלה ארוכה ממוספרת
    return True

def looks_like_answer(line: str, has_question: bool) -> bool:
    t = clean_text(line)
    if not t or not has_question:
        return False

    # סקאלה ממוספרת
    if SCALE_ANSWER_RE.match(t):
        return True

    # בולט
    if BULLET_RE.match(t):
        return True

    # טקסט רגיל – תשובה אם לא נראה כמו שאלה
    if not looks_like_question(t):
        return True

    return False

def infer_question_type(answers: List[str]) -> str:
    if not answers:
        return "open"
    if any(re.match(r"^\d+", a) for a in answers):
        return "single_choice"
    return "single_choice"

# -----------------------------
# DOCX parsing (מותאם למסמך שלך)
# -----------------------------
def parse_docx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    try:
        from docx import Document
    except Exception:
        raise RuntimeError("python-docx is not installed")

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [clean_text(p.text) for p in doc.paragraphs]
    paragraphs = [p for p in paragraphs if p]

    questions: List[Dict[str, Any]] = []
    current_q: Optional[Dict[str, Any]] = None

    def flush():
        nonlocal current_q
        if not current_q:
            return

        qtext = strip_prefixes(current_q["text"])
        answers = [strip_prefixes(a) for a in current_q["answers"] if strip_prefixes(a)]

        questions.append({
            "text": qtext,
            "type": infer_question_type(answers),
            "answers": answers,
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
            flush()
            current_q = {
                "text": line,
                "answers": []
            }
            i += 1
            continue

        if current_q:
            if looks_like_answer(line, has_question=True):
                current_q["answers"].append(line)
            else:
                # המשך שאלה ארוכה
                current_q["text"] += " " + line

        i += 1

    flush()
    return questions

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
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Only DOCX supported at this stage",
                    "filename": filename,
                },
            )

        return JSONResponse(
            {
                "status": "parsed",
                "filename": filename,
                "questions_count": len(questions),
                "questions": questions,
            }
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "filename": filename,
                "message": str(e),
            },
        )
