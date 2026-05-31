import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def _get_secret(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        try:
            import streamlit as st
            val = st.secrets.get(key, "")
        except Exception:
            pass
    return val


GROQ_MODEL = "llama-3.3-70b-versatile"

# Ask for a wrapper object so JSON mode works cleanly
_PROMPT_TEMPLATE = """\
You are a macroeconomic analyst. Analyze these {n} news articles for their economic significance.

{articles}

Return a JSON object with a single key "articles" containing an array with one element per article \
(in the same order). Each element must have exactly these keys:
  "article_index": integer (1-based),
  "econ_relevance": number 0.0-1.0 (how directly this affects macroeconomic conditions),
  "impact_direction": one of "positive", "negative", "neutral", "mixed",
  "impact_magnitude": number 0.0-1.0 (potential size of economic effect),
  "ai_summary": string (2-3 sentences on economic implications),
  "reasoning": string (1 sentence explaining scores)

Score HIGH relevance for: monetary policy, inflation, employment, GDP, trade policy, \
tariffs, sanctions, central bank actions, fiscal policy, geopolitical events with \
supply-chain or market consequences (war, sanctions, major trade deals).
Score LOW relevance for: celebrity news, sports, local crime, lifestyle articles.\
"""


def get_groq_client():
    from groq import Groq
    key = _get_secret("GROQ_API_KEY")
    if not key or key == "your_groq_key_here":
        raise ValueError(
            "GROQ_API_KEY not set. Add it to .env — free key at console.groq.com"
        )
    return Groq(api_key=key)


def groq_key_configured() -> bool:
    key = _get_secret("GROQ_API_KEY")
    return bool(key) and key != "your_groq_key_here"


def _extract_array(text: str) -> list:
    """Best-effort extraction of a JSON array from messy LLM output."""
    # Try the wrapper object first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Find the first '[' ... ']' span using bracket depth tracking
    start = text.find('[')
    if start == -1:
        return []
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break

    # Last resort: pull out individual {...} objects
    objects = []
    for match in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        try:
            objects.append(json.loads(match.group()))
        except json.JSONDecodeError:
            continue
    return objects


def analyze_batch(groq_client, articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    lines = []
    for i, a in enumerate(articles, 1):
        summary = (a.get("raw_summary") or "").strip()
        snippet = f" | {summary[:200]}" if summary else ""
        lines.append(f"[{i}] {a['source']} | {a['title']}{snippet}")

    prompt = _PROMPT_TEMPLATE.format(
        n=len(articles),
        articles="\n".join(lines),
    )

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=6000,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()
    return _extract_array(raw)
