from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import re
import io
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

app = FastAPI()

# --------------------------------------------------
# CORS (כדי ש-Base44 יוכל לקרוא)
# --------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# Load external rules config
# --------------------------------------------------
CONFIG_PATH = Path("rules/split_rules.json")

if not CONFIG_PATH.exists():
    raise RuntimeError("Missing rules/split_rules.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    SPLIT_CONFIG = json.load(f)

QUESTION_START_WORDS = SPLIT_CONFIG.get("question_start_words", [])
CONTEXT_RULES = sorted(
    SPLIT_CONFIG.get("context_question_rules", []),
    key=lambda x: x.get("priority", 0),
    reverse=True,
)
MAX_CONTEXT_LENGTH = SPLIT_CONFIG.get("max_context_length", 200)

# --------------------------------------------------
# Healthcheck
# --------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "service": "LTG Questionnaire Extractor"}

# --------------------------------------------------
# Helpers
# --------------------------------------------------
QUESTION_PATTERNS = [
    r"^\s*\d+[\)\.\-]\s+",
    r"^\s*[א-ת]\)\s+",
]

ANSWER_PREFIX_PATTERNS = [
    r"^\s*[\u2022\-\*]\s+",
    r"^\s*\d+[\)\.\-]\s+",
    r"^\s*[א-ת]\)\s+",
]

MULTI_CHOICE_HINTS = [
    "אפשר לבחור",
    "יותר מתשובה אחת",
    "מספר תשובות",
    "בחר/י עד",
    "בחרו עד",
    "סמן/י את כל",
]

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def strip_prefixes(s: str) -> str:
    t = s
    for p in QUESTION_PATTERNS + ANSWER_PREFIX_PATTERNS:
        t = re.sub(p, "", t)
    return clean_text(t)

def looks_like_question(line: str) -> bool:
    t = clean_text(line)
    if len(t) < 5:
        return False
    if t.endswith("?"):
        return True
    for w in QUESTION_START_WORDS:
        if t.startswith(w):
            return True
    for p in QUESTION_PATTERNS:
        if re.search(p, t) and len(t) > 10:
            return True
    return False

def looks_like_answer(line: str) -> bool:
    t = clean_text(line)
    if not t:
        return False
    for p in ANSWER_PREFIX_PATTERNS:
        if re.search(p, t):
            return True
    return len(t) <= 50 and not looks_like_question(t)

def infer_question_type(question_text: str, answers: List[str]) -> str:
    qt = clean_text(question_text)
    if not answers:
        return "open"
    for hint in MULTI_CHOICE_HINTS:
        if hint in qt:
            return "multi_choice"
    return "single_choice"

# --------------------------------------------------
# Context-aware splitting using rules
# --------------------------------------------------
def split_context_and_question(text: str) -> str:
    t = clean_text(text)

    if len(t) <= MAX_CONTEXT_LENGTH:
        return t

    for rule in CONTEXT_RULES:
        pattern = rule.get("pattern")
        action = rule.get("action")

        match = re.search(pattern, t)
        if not match:
            continue

        if action == "split_before_match":
            return t[: match.start()].strip()

        if action == "split_at_question_word":
            for w in QUESTION_START_WORDS:
                idx = t.find(w)
                if idx > 0:
                    return t[idx:].strip()

    return t

# --------------------------------------------------
# DOCX parsing
# --------------------------------------------------
def parse_docx_questions(file_bytes: bytes) -> List[Dict[str, Any]]:
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]

    questions = []
    current_q = None

    def flush():
        nonlocal current_q
        if not current_q:
            return

        qtext = split_context_and_question(current_q["text"])
        answers = []
        seen = set()

        for a in current_q["answers"]:
            aa = strip_prefixes(a)
            if aa and aa not in seen:
                seen.add(aa)
                answers.append(aa)

        questions.append({
            "text": qtext,
            "type": infer_question_type(qtext, answers),
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
            current_q = {"text": line, "answers": []}
            i += 1
            while i < len(paragraphs):
                nxt = paragraphs[i]
                if looks_like_question(nxt):
                    break
                if looks_like_answer(nxt):
                    current_q["answers"].append(nxt)
                else:
                    current_q["text"] += " " + nxt
                i += 1
            continue

        i += 1

    flush()
    return questions

# --------------------------------------------------
# API endpoint
# --------------------------------------------------
@app.post("/extract")
async def extract_questions(file: UploadFile = File(...)):
    raw = await file.read()
    filename = file.filename or "uploaded"
    content_type = file.content_type or ""

    try:
        if filename.lower().endswith(".docx"):
            questions = parse_docx_questions(raw)
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Unsupported file type (only DOCX supported currently)",
                    "filename": filename,
                },
            )

        return JSONResponse({
            "status": "parsed",
            "filename": filename,
            "questions_count": len(questions),
            "questions": questions,
        })

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "filename": filename,
                "message": str(e),
            },
        )
