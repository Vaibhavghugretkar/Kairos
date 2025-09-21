import io
import os
import asyncio
from typing import List
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from docx import Document
import pypdf
from dotenv import load_dotenv
from gradio_client import Client
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, pipeline

from agents import gemini_risk_agent, answer_question

# -------------------------------
# Logging
# -------------------------------
logging.basicConfig(level=logging.INFO)

# -------------------------------
# Load environment variables
# -------------------------------
load_dotenv()

# -------------------------------
# FastAPI app
# -------------------------------
app = FastAPI(
    title="LexiClarus API",
    description="Analyze legal contracts: split clauses, simplify, flag risks, and Q&A."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# -------------------------------
# Pydantic models
# -------------------------------
class ClauseAnalysis(BaseModel):
    clause_number: int
    original_clause: str
    simplified_text: str
    risks: List[str] = []  

class AnalysisResponse(BaseModel):
    filename: str
    content_type: str
    clauses: List[ClauseAnalysis]

class QARequest(BaseModel):
    question: str
    context: str

# -------------------------------
# Helpers: extract text
# -------------------------------
def extract_text_from_docx_bytes(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paras)

def extract_text_from_pdf_bytes(data: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages_text = [p.extract_text() for p in reader.pages if p.extract_text()]
    return "\n\n".join(pages_text)

# -------------------------------
# LLM for clause extraction (Flan-T5)
# -------------------------------
t5_model_id = "google/flan-t5-base"
tokenizer = AutoTokenizer.from_pretrained(t5_model_id)
model = AutoModelForSeq2SeqLM.from_pretrained(t5_model_id)
flan_pipe = pipeline("text2text-generation", model=model, tokenizer=tokenizer)

def extract_clauses_flan(text: str) -> List[str]:
    words = text.split()
    chunks = []
    current_chunk = []
    for word in words:
        current_chunk.append(word)
        tokens = tokenizer(" ".join(current_chunk), return_tensors="pt").input_ids
        if tokens.shape[1] > 512:
            current_chunk.pop()
            chunks.append(" ".join(current_chunk))
            current_chunk = [word]
    if current_chunk:
        chunks.append(" ".join(current_chunk))

    clauses = []
    for chunk in chunks:
        prompt = f"Extract individual legal clauses from the following text and return as a JSON array:\n\n{chunk}"
        result = flan_pipe(prompt, max_length=1024, do_sample=False)[0]['generated_text']
        try:
            import json
            extracted = json.loads(result)
            if isinstance(extracted, list):
                clauses.extend(extracted)
            else:
                clauses.append(chunk)
        except Exception:
            clauses.append(chunk)
    return clauses

# -------------------------------
# HF Space client for simplification
# -------------------------------
HF_CLIENT = Client("Aryan-2511/LexiClarus")

async def simplify_clauses_async(clauses: List[str]) -> List[str]:
    loop = asyncio.get_event_loop()
    async def simplify_single(clause: str) -> str:
        return await loop.run_in_executor(
            None,
            lambda: HF_CLIENT.predict(clause, api_name="/simplify_clause")
        )
    tasks = [simplify_single(c) for c in clauses]
    return await asyncio.gather(*tasks)

# -------------------------------
# Orchestrator
# -------------------------------
async def run_full_analysis(full_text: str):
    # 1. Extract clauses
    clauses = extract_clauses_flan(full_text)
    if not clauses:
        raise HTTPException(status_code=400, detail="No clauses identified.")
    logging.info(f"🤖 Extracted {len(clauses)} clauses")

    # 2. Simplify clauses
    simplified_list = await simplify_clauses_async(clauses)

    # 3. Risk analysis with fallback
    results = []
    for i, clause in enumerate(clauses):
        simplified = simplified_list[i] if i < len(simplified_list) else clause

        try:
            risk_flags = await gemini_risk_agent(clause) or []
        except Exception as e:
            logging.warning(f"⚠️ Risk analysis failed for clause {i+1}: {e}")
            risk_flags = []

        results.append({
            "clause_number": i + 1,
            "original_clause": clause,
            "simplified_text": simplified,
            "risk_flags": risk_flags  
        })

    return results

# -------------------------------
# FastAPI endpoints
# -------------------------------
@app.get("/", tags=["Health Check"])
async def root():
    return {"message": "LexiClarus API running."}

@app.post("/analyze-document/", response_model=AnalysisResponse, tags=["Document Analysis"])
async def analyze_document(file: UploadFile = File(...)):
    filename = file.filename or "uploaded_doc"
    content_type = file.content_type or ""
    logging.info(f"📥 Received file: {filename} ({content_type})")

    raw_bytes = await file.read()
    try:
        if filename.lower().endswith(".pdf"):
            full_text = extract_text_from_pdf_bytes(raw_bytes)
        elif filename.lower().endswith(".docx"):
            full_text = extract_text_from_docx_bytes(raw_bytes)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF or DOCX.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error extracting text: {e}")

    clauses = await run_full_analysis(full_text)
    return AnalysisResponse(filename=filename, content_type=content_type, clauses=clauses)

@app.post("/ask-question/")
async def qa_endpoint(req: QARequest):
    try:
        answer = await answer_question(req.question, req.context)
        return {"question": req.question, "answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"QA failed: {e}")
