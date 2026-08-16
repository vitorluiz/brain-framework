# Quickstart — Brain Framework

> Primeiro uso em 5 minutos.

---

## Instalação

```bash
git clone git@github.com:vitorluiz/brain-framework.git
cd brain-framework
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Para suporte a PDF/DOCX/planilhas no `learn`:

```bash
pip install -e ".[learn]"
```

---

## Verificar a instalação

```bash
brain --version        # brain 1.2.0
brain-tool --version   # brain-tool 1.2.0
brain --help
```

---

## Primeiro uso

```bash
# 1. Criar um expert (profile Hermes + brain.db + alias)
brain add profile maria

# 2. Adicionar conhecimento
brain remember --expert maria --tipo fact \
  --title "Horário" --content "Segunda a sexta, 8h-18h"

# 3. Consultar
brain recall --expert maria --search "Horário"

# 4. Popular o global (compartilhado entre experts)
brain global learn --content "Feriados da empresa" --title "Feriados" --sync

# 5. Ingerir arquivos com sync automático
brain learn --expert maria --path /documentos/atendimento/ --sync

# 6. Ver integridade e jobs
brain check --expert maria
brain jobs  --expert maria
```

---

## Estrutura esperada

```
~/.hermes/brain/
├── global/brain.db
├── experts/maria/brain.db
├── admins.json
└── backups/
```

Defina `BRAIN_ROOT` para usar outra raiz (útil em testes/containers).

---

## Próximos passos

- [README](./README.md) — conceitos
- [commands.md](./commands.md) — referência completa
- [plan/](../plan/) — especificação e roadmap
