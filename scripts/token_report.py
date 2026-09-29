#!/usr/bin/env python
"""Reporte determinista de tokens del prefijo del agente de Kohlberg (sin llamar a OpenAI).

Mide lo que se re-envía en CADA llamada al LLM: el system prompt + los schemas de las
tools. Es el oráculo antes/después para la optimización de tokens (spec
docs/spec-kohlberg-optimizacion-tokens.md): corré esto, anotá el total, aplicá un cambio,
volvé a correr y compará. No hace ninguna llamada a la API.

Además distingue:
  - prefijo ESTÁTICO (system prompt sin el bloque volátil + tools) → lo que OpenAI puede
    cachear si el datetime/contexto va al final (C1). Si esto es estable entre llamadas,
    el prompt-cache pega.
  - prompt ENSAMBLADO completo (con datetime + contexto de contacto).

Uso:
    uv run python scripts/token_report.py                 # imprime el desglose
    uv run python scripts/token_report.py --budget 6000   # exit≠0 si el prefijo estático supera el techo
        (para usar como gate en CI / tests de presupuesto)
"""

import argparse
import json
import os
import sys

# Este script se corre directo (`python scripts/token_report.py`), así que la raíz del repo
# no está en sys.path. La agregamos antes de importar `app` (shim estándar de entry-point).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tiktoken
except ImportError:  # pragma: no cover - dependencia de langchain_openai
    print("ERROR: falta tiktoken (viene con langchain_openai). Instalá deps con `make install`.", file=sys.stderr)
    sys.exit(1)

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core.langgraph.kohlberg_graph import (
    _KOHLBERG_TOOLS,
    _PROMPT_TEMPLATE,
    _load_kohlberg_prompt,
)

_MODEL = "gpt-4o-mini"


def _encoding() -> "tiktoken.Encoding":
    try:
        return tiktoken.encoding_for_model(_MODEL)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


def _count(enc: "tiktoken.Encoding", text: str) -> int:
    return len(enc.encode(text))


def main() -> None:
    """Imprime el desglose de tokens del prefijo; con --budget, falla si el estático lo supera."""
    ap = argparse.ArgumentParser(description="Reporte de tokens del prefijo del agente de Kohlberg.")
    ap.add_argument("--budget", type=int, default=0, help="Techo de tokens del prefijo estático; exit≠0 si lo supera.")
    args = ap.parse_args()

    enc = _encoding()

    # Prompt estático (la plantilla sin resolver el datetime) = lo cacheable si lo volátil va al final.
    static_prompt_tokens = _count(enc, _PROMPT_TEMPLATE)
    # Prompt ensamblado real (con datetime + contexto de contacto al final).
    assembled = _load_kohlberg_prompt("token-report", conversation_id=0, channel="report")
    assembled_tokens = _count(enc, assembled)

    print(f"Modelo: {_MODEL}\n")
    print("SYSTEM PROMPT")
    print(f"  estático (cacheable)     : {static_prompt_tokens:6d} tok")
    print(f"  ensamblado (con volátil) : {assembled_tokens:6d} tok\n")

    print("TOOLS (schemas OpenAI, enviados en cada llamada)")
    tools_total = 0
    for tool in _KOHLBERG_TOOLS:
        schema = convert_to_openai_tool(tool)
        n = _count(enc, json.dumps(schema, ensure_ascii=False))
        name = schema.get("function", {}).get("name", getattr(tool, "name", "?"))
        tools_total += n
        print(f"  {name:26s} {n:6d} tok")
    print(f"  {'TOTAL tools':26s} {tools_total:6d} tok\n")

    prefix_static = static_prompt_tokens + tools_total
    prefix_assembled = assembled_tokens + tools_total
    print("PREFIJO POR LLAMADA (system + tools)")
    print(f"  estático (cacheable)     : {prefix_static:6d} tok")
    print(f"  ensamblado               : {prefix_assembled:6d} tok")
    print("\n(no incluye el historial de la conversación, que crece por turno)")

    if args.budget and prefix_static > args.budget:
        print(f"\n❌ Prefijo estático {prefix_static} > presupuesto {args.budget}.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
