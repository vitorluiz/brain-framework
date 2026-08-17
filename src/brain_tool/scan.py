"""Detecção heurística de conteúdo suspeito (prompt injection / credenciais / PII).

Não é filtro infalível — o objetivo é **sinalizar** risco para o `diff`/`approve`
e reduzir publicação automática de conteúdo potencialmente malicioso.
Os padrões são conservadores e podem gerar falsos positivos; por isso os
resultados são *flags* exibidas ao admin, nunca bloqueio silencioso.
"""

from __future__ import annotations

import re
from typing import Dict, List

# Instruções típicas de prompt injection (indireto, via conteúdo importado).
_INSTRUCTION_PATTERNS = [
    re.compile(r"ignore\s+(?:as\s+)?(?:regras|instru[çc][õo]es|todas\s+as\s+instru[çc][õo]es)", re.I),
    re.compile(r"ignore\s+(?:everything\s+)?(?:above|before|previous|all\s+prior)", re.I),
    re.compile(r"desconsidere\s+(?:as\s+)?(?:regras|instru[çc][õo]es)", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:prior|previous|above)\s+instructions", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"developer\s+message", re.I),
    re.compile(r"role\s*:\s*['\"]?system['\"]?", re.I),
]

# Credenciais / segredos.
_CREDENTIAL_PATTERNS = [
    re.compile(r"\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|"
               r"client[_-]?secret|private[_-]?key)\b", re.I),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
]

# PII (heurístico — falsos positivos esperados).
_PII_PATTERNS = [
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),            # CPF
    re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),      # CNPJ
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),      # cartão
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # e-mail
]

_MAX_MATCHES = 20


def _matches(patterns: List[re.Pattern], text: str) -> List[str]:
    found: List[str] = []
    for p in patterns:
        for m in p.finditer(text):
            found.append(m.group(0)[:80])
    return sorted(set(found))[:_MAX_MATCHES]


def scan_content(text: str) -> Dict[str, object]:
    """Retorna flags de risco: instruções, credenciais e PII detectadas."""
    instructions = _matches(_INSTRUCTION_PATTERNS, text)
    credentials = _matches(_CREDENTIAL_PATTERNS, text)
    pii = _matches(_PII_PATTERNS, text)
    return {
        "instructions": instructions,
        "credentials": credentials,
        "pii": pii,
        "suspicious": bool(instructions or credentials or pii),
    }


def merge_scans(scans: List[Dict[str, object]]) -> Dict[str, object]:
    """Consolida múltiplos scans (ex.: vários arquivos de um diretório)."""
    merged: Dict[str, set] = {"instructions": set(), "credentials": set(), "pii": set()}
    for s in scans:
        if not s:
            continue
        for key in ("instructions", "credentials", "pii"):
            value = s.get(key)
            if isinstance(value, list):
                merged[key].update(value)
    result: Dict[str, object] = {k: sorted(v)[:_MAX_MATCHES] for k, v in merged.items()}
    result["suspicious"] = any(result.values())
    return result
