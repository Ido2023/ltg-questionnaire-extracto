from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

# --- CORS (קריטי ל-Base44) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # בהמשך נצמצם
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- בדיקת חיים ---
@app.get("/")
def root():
    return {"status": "ok", "service": "LTG Questionnaire Extractor"}

# --- ENDPOINT שה-UI צריך ---
@app.post("/extract")
async def extract_questions(file: UploadFile = File(...)):
    content = await file.read()

    text = content.decode("utf-8", errors="ignore")

    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 10]

    questions = []
    for line in lines:
        if "?" in line or "?" in line:
            questions.append({
                "text": line,
                "type": "open",
                "answers": []
            })

    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "status": "parsed",
        "questions": questions
    }

