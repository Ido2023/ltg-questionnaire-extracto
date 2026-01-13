from fastapi import FastAPI, UploadFile, File, HTTPException

app = FastAPI(title="LTG Questionnaire Extractor")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/extract/docx")
async def extract_docx(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    content = await file.read()

    return {
        "filename": file.filename,
        "size_bytes": len(content),
        "status": "received"
    }
