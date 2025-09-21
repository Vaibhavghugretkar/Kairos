"""
Agent layer for LexiClarus with concurrency limiting for HF Space calls.

"""

import os
import re
import asyncio
import json
import ast
from typing import List, Optional
from dotenv import load_dotenv
from gradio_client import Client

# Load .env
load_dotenv()

# -------------------------
# Config
# -------------------------
HF_SPACE_ID = os.environ.get("HF_SPACE_ID", "Aryan-2511/LexiClarus")
HF_SPACE_TOKEN = os.environ.get("HF_SPACE_TOKEN")
HF_CONCURRENCY = int(os.environ.get("HF_CONCURRENCY", "5"))

HF_MAX_RETRIES = int(os.environ.get("HF_MAX_RETRIES", "3"))
HF_RETRY_BACKOFF = float(os.environ.get("HF_RETRY_BACKOFF", "1.0"))
HF_BATCH_SIZE = int(os.environ.get("HF_BATCH_SIZE", "5"))


# Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai 
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"⚠️ Could not import google.generativeai: {e}")
        genai = None

# Risk flags
VALID_RISK_FLAGS = [
    "penalty",
    "fee",
    "auto-renewal",
    "arbitration",
    "liability",
    "lock-in-period",
    "unilateral-change",
    "security-deposit-deduction",
]

# Globals
_hf_client: Optional[Client] = None
_hf_sem: Optional[asyncio.Semaphore] = None


# -------------------------
# Helpers
# -------------------------
def _get_client() -> Client:
    """Lazy-load HF client."""
    global _hf_client
    if _hf_client is None:
        print(f"🔗 Connecting to HF Space: {HF_SPACE_ID}")
        if HF_SPACE_TOKEN:
            _hf_client = Client(HF_SPACE_ID, hf_token=HF_SPACE_TOKEN)
        else:
            _hf_client = Client(HF_SPACE_ID)
    return _hf_client


def _get_semaphore() -> asyncio.Semaphore:
    global _hf_sem
    if _hf_sem is None:
        _hf_sem = asyncio.Semaphore(HF_CONCURRENCY)
    return _hf_sem


async def _call_hf_space_with_retries(clause: str) -> str:
    """Call HF Space simplify endpoint with retries + concurrency limit."""
    sem = _get_semaphore()
    attempt = 0
    backoff = HF_RETRY_BACKOFF

    while attempt < HF_MAX_RETRIES:
        attempt += 1
        async with sem:
            try:
                client = _get_client()
                result = await asyncio.to_thread(
                    client.predict,
                    clause,
                    api_name="/simplify_clauses"
                )
                if isinstance(result, str) and result.strip():
                    return result.strip()
                elif isinstance(result, list) and result:
                    return str(result[0])
                print(f"⚠️ Empty result (attempt {attempt})")
            except Exception as e:
                print(f"⚠️ HF call failed (attempt {attempt}): {e}")

        await asyncio.sleep(backoff)
        backoff *= 2

    print("❌ HF simplifier failed, returning original clause.")
    return clause


# -------------------------
# Agents
# -------------------------
async def identify_clauses(full_text: str) -> List[str]:
    """Split clauses (Gemini or heuristic)."""
    print("🤖 Identifying clauses...")
    if genai:
        try:
            model = genai.GenerativeModel("gemini-2.0-flash")
            prompt = f"""
Split the following legal document into distinct clauses.
Output must be JSON list of strings only.

---
{full_text}
---
"""
            response = await model.generate_content_async(prompt)
            raw = response.text.strip().replace("```json", "").replace("```", "")
            clauses = json.loads(raw)
            if isinstance(clauses, list):
                return [c.strip() for c in clauses if c.strip()]
        except Exception as e:
            print(f"⚠️ Gemini failed: {e}, using fallback.")

    candidates = [p.strip() for p in full_text.split("\n\n") if p.strip()]
    return candidates or [full_text]


async def _call_hf_space_batch(clauses: List[str]) -> List[str]:
    """Call HF Space once with a batch of clauses."""
    client = _get_client()
    try:
        result = await asyncio.to_thread(
            client.predict,
            clauses,  
            api_name="/simplify_clauses"
        )
        if isinstance(result, list):
            return [str(r).strip() for r in result]
        if isinstance(result, str):
            return [r.strip() for r in result.split("\n") if r.strip()]
    except Exception as e:
        print(f"⚠️ HF batch call failed: {e}")
    return clauses  

async def simplify_clauses(clauses: List[str]) -> List[str]:
    print(f"🤖 Simplifying {len(clauses)} clauses (batch size={HF_BATCH_SIZE})...")
    results: List[str] = []

    for i in range(0, len(clauses), HF_BATCH_SIZE):
        batch = clauses[i:i+HF_BATCH_SIZE]
        print(f"📤 Sending batch {i//HF_BATCH_SIZE + 1} with {len(batch)} clauses...")
        simplified_batch = await _call_hf_space_batch(batch)
        print(f"✅ Batch {i//HF_BATCH_SIZE + 1} done.")
        if len(simplified_batch) != len(batch):
            simplified_batch = batch
        results.extend(simplified_batch)

    return results



async def gemini_risk_agent(clause: str) -> List[str]:
    """Detect risks in a clause."""
    print("🤖 Risk analysis...")
    if genai:
        try:
            model = genai.GenerativeModel("gemini-2.0-flash")
            prompt = f"""
Identify risks in the clause. Only return a Python list with items from:
{VALID_RISK_FLAGS}

Clause:
{clause}
"""
            response = await model.generate_content_async(prompt)
            raw = response.text.strip()
            return [f for f in ast.literal_eval(raw) if f in VALID_RISK_FLAGS]
        except Exception as e:
            print(f"⚠️ Gemini risk failed: {e}, using fallback.")

    # Fallback
    risks = []
    low = clause.lower()
    if "penalty" in low or "fine" in low:
        risks.append("penalty")
    if "fee" in low or "charges" in low:
        risks.append("fee")
    if "auto-renew" in low:
        risks.append("auto-renewal")
    return risks


# -------------------------
# Q&A Heuristics
# -------------------------
def _find_period_of_months(context: str) -> Optional[str]:
    m = re.search(r'period\s+of\s+(\d{1,3})\s*(months?|years?)', context, re.I)
    return f"{m.group(1)} {m.group(2)}" if m else None


def _find_start_end_dates(context: str) -> Optional[tuple]:
    m = re.search(r'from\s+(.*?)\s+(?:to|until)\s+(.*?)(?:\.|,|$)', context, re.I)
    return (m.group(1), m.group(2)) if m else None


async def answer_question(question: str, context: str) -> str:
    """Answer questions using Gemini or regex heuristics."""
    print("🤖 Q&A Agent...")
    if genai:
        try:
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = await model.generate_content_async(
                f"Answer this based ONLY on the text:\n\n{context}\n\nQ: {question}\n\nA:"
            )
            return response.text.strip()
        except Exception as e:
            print(f"⚠️ Gemini Q&A failed: {e}, using fallback.")

    low_q = question.lower()
    if "duration" in low_q or "period" in low_q or "term" in low_q:
        period = _find_period_of_months(context)
        dates = _find_start_end_dates(context)
        if period and dates:
            return f"The agreement duration is {period}, from {dates[0]} to {dates[1]}."
        if period:
            return f"The agreement duration is {period}."
        if dates:
            return f"The agreement runs from {dates[0]} to {dates[1]}."
    return "The answer to this question is not found in the document."
