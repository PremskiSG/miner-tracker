"""DeepSeek extraction via DeepSeek's Anthropic-compatible endpoint (the same
pattern as the user's segment-inflection/llm_utils.py). No native PDF input,
so the PDF is converted to text first; no structured-outputs enforcement, so
the JSON is requested in the prompt and normalized post-parse."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import anthropic

from miner_tracker.extraction.extractor import ExtractionResult
from miner_tracker.extraction.parsing import normalize, parse_json
from miner_tracker.extraction.pdftext import pdf_to_text
from miner_tracker.extraction.prompts import PROMPTS, SYSTEM
from miner_tracker.extraction.schemas import SCHEMAS
from miner_tracker.secrets import get_secret

logger = logging.getLogger("miner_tracker.deepseek")

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"

# USD per MTok (input, output) — from the user's llm_utils registry
PRICES = {
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-v4-flash": (0.14, 0.28),
}


def _client() -> anthropic.Anthropic:
    key = get_secret("deepseek", "api_key", env_var="DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "No DeepSeek key: set DEEPSEEK_API_KEY or add to secrets.yaml:\n"
            "deepseek:\n  api_key: \"...\"")
    base = get_secret("deepseek", "base_url", env_var="DEEPSEEK_BASE_URL") \
        or DEFAULT_BASE_URL
    return anthropic.Anthropic(api_key=key, base_url=base)


def extract_pdf(pdf_path: Path, doc_type: str, company_name: str,
                published_date: str, model: str, max_tokens: int = 8192,
                metal: str = "silver") -> ExtractionResult:
    text = pdf_to_text(pdf_path, doc_type)
    if not text.strip():
        raise ValueError("PDF produced no extractable text")
    schema = SCHEMAS[doc_type]
    prompt = (
        PROMPTS[doc_type](company_name, published_date, metal)
        + "\n\nRespond with ONLY a JSON object (no prose, no markdown fences) that "
        "conforms exactly to this JSON schema:\n"
        + json.dumps(schema)
        + "\n\nDocument text (page markers indicate PDF page numbers):\n\n"
        + text
    )
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM,
        thinking={"type": "disabled"},   # required by DeepSeek's endpoint
        messages=[{"role": "user", "content": prompt}],
    )
    raw = next((b.text for b in response.content if b.type == "text"), "")
    data = normalize(parse_json(raw), doc_type)
    usage = response.usage
    inp, out = PRICES.get(model, (0.0, 0.0))
    return ExtractionResult(
        data=data, model=model,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        cost_usd=usage.input_tokens / 1e6 * inp + usage.output_tokens / 1e6 * out,
        raw_text=raw,
    )
