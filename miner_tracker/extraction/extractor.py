"""Claude API call: base64 PDF document block + strict json_schema structured
output. Pure I/O against the API — no database access here."""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import anthropic

from miner_tracker.extraction.prompts import PROMPTS, SYSTEM
from miner_tracker.extraction.schemas import SCHEMAS
from miner_tracker.secrets import anthropic_api_key

logger = logging.getLogger("miner_tracker.extractor")

# USD per MTok (input, output)
PRICES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
}


@dataclass
class ExtractionResult:
    data: dict
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    raw_text: str


def _client() -> anthropic.Anthropic:
    key = anthropic_api_key()
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Cheap heuristic page count (used only to pre-route long docs)."""
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES.get(model, (0.0, 0.0))
    return input_tokens / 1e6 * inp + output_tokens / 1e6 * out


def extract_pdf(pdf_path: Path, doc_type: str, company_name: str,
                published_date: str, model: str, max_tokens: int = 8192) -> ExtractionResult:
    """Send one PDF to Claude and return the schema-validated dict.

    Raises anthropic.* API errors and ValueError (unparseable JSON) — the
    pipeline decides whether to escalate to the fallback model.
    """
    schema = SCHEMAS[doc_type]
    prompt = PROMPTS[doc_type](company_name, published_date)
    pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")

    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf",
                            "data": pdf_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise ValueError("model refused the request")
    if response.stop_reason == "max_tokens":
        raise ValueError("output truncated at max_tokens")

    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)  # json_schema output_config guarantees valid JSON
    usage = response.usage
    return ExtractionResult(
        data=data,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=cost_usd(model, usage.input_tokens, usage.output_tokens),
        raw_text=text,
    )
