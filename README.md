# Brain — Expert Nativo do Brain Framework

**CLI versátil + camada de gestão de Brain** para qualquer usuário.

Autores: [Vitor Luiz](mailto:vitorluizmachado@gmail.com)

> O Brain é o *expert* especial nativo do framework. Ele não é um agente de
> atendimento (como Maria ou José). Ele é o gestor nativo — o "cérebro" que
> gerencia os outros cérebros.

---

## O que é

O Brain é um CLI (linha de comando) que gerencia bases de conhecimento
SQLite (`brain.db`). Ele substitui o agente *default* do [Hermes Agent] e
providencia toda a infraestrutura de conhecimento: criação de *profiles*,
manipulação de conhecimento, backup, sincronização, administradores.

É um artefato **universal e público**, não vinculado a nenhuma aplicação
particular. Qualquer pessoa pode instalar e usar.

---

## Instalação

### Via Python (venv)

```bash
# 1. Clone
git clone https://github.com/vitorluiz/brain-framework.git
cd brain-framework

# 2. Crie e ative o venv
python3 -m venv .venv
source .venv/bin/activate

# 3. Instale
pip install -e .
```

### Via Docker (opcional)

(Coming soon.)

---

## Primeiros passos

```bash
# Criar um profile (ex: maria)
brain add profile maria

# Inicializar um brain.db
brain init --name maria

# Adicionar conhecimento
brain remember --expert maria \
  --tipo memory \
  --title "Quem é Maria" \
  --content "Maria é um agente de atendimento simpático."

# Consultar
brain recall --expert maria --search "Maria"
```

---

## Estrutura de arquivos

```
~/.hermes/
├── profiles/              # Perfis do Hermes Agent (config.yaml, SOUL.md)
│   └── <nome>/
└── brain/                 # Bases de conhecimento (brain.db)
    ├── <nome>/            # Brain de cada expert (espelha profiles/<nome>/)
    │   └── brain.db
    ├── global/            # Brain global (compartilhado entre experts)
    │   └── brain.db
    ├── admins.json        # Lista de administradores
    └── backups/           # Backups gerados por `brain backup`
```

O `brain add profile <nome>` cria **ambos**:
1. Profile em `~/.hermes/profiles/<nome>/`
2. Brain.db em `~/.hermes/brain/<nome>/brain.db`

O **Brain** (expert nativo) substitui o agente default do Hermes — o `SOUL.md`
dele vive em `doc/SOUL.md` e é implantado em `~/.hermes/SOUL.md`. O manual de
comandos não fica em um AGENTS.md solto: ele vive na própria base global
(`brain remember --global --tipo reference --title "Comandos brain" ...`),
consultável via `brain recall --global --search "Comandos"`.

---

## Comandos principais

### Gestão de profiles
```bash
brain add profile <nome>
brain list profiles
brain remove profile <nome>
```

### Conhecimento
```bash
brain init --name <nome>
brain remember --expert <e> --tipo <t> --title <t> --content <c>
brain recall --expert <e> [--search <termo>]
brain forget --expert <e> --id <N>
brain synthesize --expert <e>
brain consolidate --expert <e> [--dry-run]
brain learn --expert <e> --path <arquivo/dir>
brain sync-tb --expert <e>
brain check --expert <e>
```

### Sincronização e backup
```bash
brain sync all
brain backup
```

### Administração
```bash
brain admin list
brain admin add <tipo> <id>
brain admin remove <id>
```

Veja `brain <comando> --help` para detalhes de cada um.

---

## Projeto

- **Repositório**: <https://github.com/vitorluiz/brain-framework>
- **Licença**: MIT
- **Versionamento**: Semver

---

## Status

Em evolução ativa. Veja o [plan/] para roadmap e especificações.

[Hermes Agent]: https://github.com/hermes-agent/hermes-agent (link simbólico — substituir pela URL real se disponível)
