from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic
import os
import json
import re
import pdfplumber
import io

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your Netlify URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

class ResumeRequest(BaseModel):
    resume: str
    job_description: str
    paid: bool = False

class FreeAnalysisRequest(BaseModel):
    resume: str
    job_description: str


def extract_pdf_text(file_bytes: bytes) -> str:
    """Extract plain text from PDF bytes using pdfplumber."""
    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts).strip()


FREE_PROMPT = """
You are an expert ATS (Applicant Tracking System) analyzer. Analyze the resume against the job description.

Return ONLY a valid JSON object, no markdown, no explanation, just raw JSON:
{{
  "score": <integer 0-100>,
  "verdict": "<one punchy sentence about the resume's ATS compatibility>",
  "top_issue": "<the single biggest problem killing this resume's ATS score>",
  "keyword_match_percent": <integer 0-100>,
  "matched_keywords": ["<keyword1>", "<keyword2>", "<keyword3>"],
  "missing_keywords": ["<keyword1>", "<keyword2>", "<keyword3>"]
}}

RESUME:
{resume}

JOB DESCRIPTION:
{job_description}
"""

PAID_PROMPT = """
You are an expert ATS (Applicant Tracking System) analyzer and resume coach. Analyze the resume against the job description in detail.

Return ONLY a valid JSON object, no markdown, no explanation, just raw JSON:
{{
  "score": <integer 0-100>,
  "verdict": "<one punchy sentence about the resume's ATS compatibility>",
  "keyword_match_percent": <integer 0-100>,
  "matched_keywords": ["<up to 8 keywords>"],
  "missing_keywords": ["<up to 8 missing critical keywords>"],
  "section_scores": {{
    "skills": <0-100>,
    "experience": <0-100>,
    "education": <0-100>,
    "formatting": <0-100>
  }},
  "fixes": [
    {{
      "priority": "HIGH",
      "title": "<short fix title>",
      "problem": "<what's wrong>",
      "action": "<exactly what to do, specific and actionable>"
    }},
    {{
      "priority": "HIGH",
      "title": "<short fix title>",
      "problem": "<what's wrong>",
      "action": "<exactly what to do, specific and actionable>"
    }},
    {{
      "priority": "MEDIUM",
      "title": "<short fix title>",
      "problem": "<what's wrong>",
      "action": "<exactly what to do, specific and actionable>"
    }},
    {{
      "priority": "MEDIUM",
      "title": "<short fix title>",
      "problem": "<what's wrong>",
      "action": "<exactly what to do, specific and actionable>"
    }},
    {{
      "priority": "LOW",
      "title": "<short fix title>",
      "problem": "<what's wrong>",
      "action": "<exactly what to do, specific and actionable>"
    }}
  ],
  "rewrite_tip": "<one concrete bullet point the user can rewrite in their resume RIGHT NOW, with before/after example>"
}}

RESUME:
{resume}

JOB DESCRIPTION:
{job_description}
"""


@app.get("/")
def root():
    return {"status": "ATS Checker API is running"}


@app.post("/extract-pdf")
async def extract_pdf(file: UploadFile = File(...)):
    """Extract text from uploaded PDF resume."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:  # 5MB limit
        raise HTTPException(status_code=400, detail="File too large. Max 5MB.")
    
    try:
        text = extract_pdf_text(contents)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read PDF: {str(e)}")
    
    if len(text) < 100:
        raise HTTPException(status_code=422, detail="Could not extract enough text from this PDF. Make sure it's not a scanned image.")
    
    return {"text": text, "char_count": len(text)}


@app.post("/analyze/free")
async def analyze_free(
    job_description: str = Form(...),
    file: UploadFile = File(None),
    resume_text: str = Form(None)
):
    # Accept either uploaded PDF or raw text
    if file and file.filename:
        contents = await file.read()
        try:
            resume = extract_pdf_text(contents)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not read PDF: {str(e)}")
    elif resume_text:
        resume = resume_text.strip()
    else:
        raise HTTPException(status_code=400, detail="Please upload a PDF or provide resume text.")

    if len(resume) < 100:
        raise HTTPException(status_code=400, detail="Resume content is too short. Please check your PDF.")
    if len(job_description.strip()) < 50:
        raise HTTPException(status_code=400, detail="Job description is too short.")

    prompt = FREE_PROMPT.format(
        resume=resume[:4000],
        job_description=job_description[:2000]
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse AI response. Please try again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis failed: {str(e)}")


@app.post("/analyze/paid")
async def analyze_paid(
    job_description: str = Form(...),
    paid: str = Form(...),
    file: UploadFile = File(None),
    resume_text: str = Form(None)
):
    if paid != "true":
        raise HTTPException(status_code=402, detail="Payment required.")

    # Accept either uploaded PDF or raw text
    if file and file.filename:
        contents = await file.read()
        try:
            resume = extract_pdf_text(contents)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not read PDF: {str(e)}")
    elif resume_text:
        resume = resume_text.strip()
    else:
        raise HTTPException(status_code=400, detail="Please upload a PDF or provide resume text.")

    if len(resume) < 100:
        raise HTTPException(status_code=400, detail="Resume content is too short.")
    if len(job_description.strip()) < 50:
        raise HTTPException(status_code=400, detail="Job description is too short.")

    prompt = PAID_PROMPT.format(
        resume=resume[:5000],
        job_description=job_description[:3000]
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse AI response. Please try again.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis failed: {str(e)}")