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
    # כרגע רק בדיקה – בלי לוגיקה
    return JSONResponse({
        "filename": file.filename,
        "content_type": file.content_type,
        "status": "received",
        "questions": []  # בהמשך נכניס כאן את הפלט האמיתי
    })
