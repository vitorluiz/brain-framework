"""Extração de texto — isolada (subprocess com rlimits) + pré-checagens.

`parse()` é a lógica pura (usada em-processo para tipos seguros). `main()` é o
entry point do subprocess isolado (`python -m brain_tool.extract <path>`), que
aplica limites de CPU/memória/tamanho antes de extrair — mitigação de prompt
injection via arquivos malformados (spec §9.2).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_INGEST_BYTES = int(os.environ.get("BRAIN_MAX_INGEST_BYTES", str(50 * 1024 * 1024)))

_SUPPORTED_EXTS = {".txt", ".md", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv"}


def _check_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_INGEST_BYTES:
        raise ValueError(
            f"arquivo excede o limite de {MAX_INGEST_BYTES // (1024 * 1024)}MB"
        )


def _check_magic(path: Path, ext: str) -> None:
    """Valida o MIME real por magic bytes (não confia na extensão)."""
    with open(path, "rb") as f:
        head = f.read(8)
    if ext == ".pdf" and not head.startswith(b"%PDF"):
        raise ValueError("PDF malformado (magic bytes não conferem)")
    if ext in (".docx", ".xlsx") and not head.startswith(b"PK\x03\x04"):
        raise ValueError(f"{ext} malformado (não é ZIP/Office)")


def parse(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {path}")
    ext = p.suffix.lower()
    _check_size(p)
    _check_magic(p, ext)

    if ext in (".txt", ".md"):
        return p.read_text(encoding="utf-8")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ImportError("pypdf necessario. pip install 'brain-framework[learn]'") from e
        r = PdfReader(str(p))
        return "\n".join([page.extract_text() or "" for page in r.pages])
    if ext in (".docx", ".doc"):
        try:
            from docx import Document
        except ImportError as e:
            raise ImportError("python-docx necessario. pip install 'brain-framework[learn]'") from e
        d = Document(str(p))
        return "\n".join([par.text for par in d.paragraphs])
    if ext in (".xlsx", ".xls", ".csv"):
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError("pandas necessario. pip install 'brain-framework[learn]'") from e
        if ext == ".csv":
            df = pd.read_csv(str(p))
        else:
            df = pd.read_excel(str(p))
        return df.to_string()
    return p.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "sem path"}))
        return 2
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        resource.setrlimit(resource.RLIMIT_AS, (2**30, 2**30))  # 1 GB
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_INGEST_BYTES * 2, MAX_INGEST_BYTES * 2))
    except (ImportError, ValueError):
        pass
    try:
        text = parse(sys.argv[1])
        print(json.dumps({"ok": True, "text": text}))
        return 0
    except Exception as e:  # noqa: BLE001 — o subprocess só reporta o erro
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
