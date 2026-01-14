from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from docx import Document

app = FastAPI()

# CORS – חובה ל־Base44
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "LTG Questionnaire Extractor"
    }

@app.post("/extract")
async def extract_questions(file: UploadFile = File(...)):
    questions = []

    # טיפול בקובץ Word
    if file.filename.endswith(".docx"):
        contents = await file.read()
        document = Document(contents)

        for p in document.paragraphs:
            text = p.text.strip()
            if len(text) > 10 and ("?" in text or "?" in text):
                questions.append({
                    "text": text,
                    "type": "open",
                    "answers": []
                })

    else:
        return {
            "status": "error",
            "message": "Unsupported file type"
        }

    return {
        "filename": file.filename,
        "status": "parsed",
        "questions": questions
    }
