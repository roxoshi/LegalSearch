# Architecture

  llm_providers.py — provider abstraction
  - LLMProvider abstract base with complete(case_text) -> str and _build_message() (shared prompt+text assembly)
  - AnthropicProvider, OpenAIProvider, GoogleProvider — each lazy-imports its SDK in __init__ so missing packages only fail if you
  actually try to use that provider
  - PROVIDERS registry dict + get_provider(name, **kwargs) factory for plug-and-play swapping
  - load_prompt() — reads PROMPT.md once and caches it via lru_cache

  llm_analyze.py — pipeline
  - CaseLawAnalysis Pydantic model — all 10 fields required strings, extra fields silently ignored
  - extract_text(pdf) — PyMuPDF, raises cleanly on missing/blank PDFs
  - parse_json_response(raw) — strips \``json` fences before parsing (many LLMs add these)
  - analyze_pdf(pdf, provider) — 4-step pipeline, returns typed AnalysisSuccess | AnalysisFailure
  - run_analysis(pdfs, provider, output_dir) — writes successes/*.json and failures/*_{error_type}.log
  - Full CLI via python -m pipelines.llm_analyze

# Usage

## Install LLM extras
  uv sync --extra llm

## Claude
  ANTHROPIC_API_KEY=sk-... python -m pipelines.llm_analyze \
    --input /data/cases/ --provider anthropic


## GPT-4o
  OPENAI_API_KEY=sk-... python -m pipelines.llm_analyze \
    --input case1.pdf case2.pdf --provider openai --model gpt-4o

## Gemini, custom output
  GOOGLE_API_KEY=... python -m pipelines.llm_analyze \
    --input /data/cases/ --provider google --output .data/gemini-results

# Adding a new provider

Just subclass LLMProvider, implement name and complete, and add it to PROVIDERS:

  class MyProvider(LLMProvider):
      @property
      def name(self): return "myprovider/model"
      def complete(self, case_text): ...

  PROVIDERS["myprovider"] = MyProvider
