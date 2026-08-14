# Doc — Celebro

> Este documento descreve o Celebro, o perfil mestre nativo do brain-framework.
> Não é código — é como usar Celebro, não como implementá-lo.

---

## O que é Celebro

**Celebro** é o perfil mestre nativo do brain-framework. Ele é o gestor do sistema —
não um agente comum, mas a camada que gerencia instalação, profiles, sincronização,
e backup.

Celebro é instalado com o framework e disponível como comando de sistema:

```bash
sudo celebro --version
sudo celebro update
sudo celebro add profile nome-do-profile
sudo celebro sync
sudo celebro backup
```

---

## Como Celebro funciona

### Provider padrão

- **Padrão:** Nous Research (ou provider de escolha do usuário)
- **Goal:** usar LLMs free quando possível
- **Configurável:** usuário pode mudar o provider se quiser

### Como Celebro usa o brain CLI

Celebro não reimplementa manipulação de brain.db — ele usa `brain_tool.py`:

```
celebro add profile nome-do-profile
  → brain_tool init --scope nome-do-profile --brain-dir ~/.brain/profiles/nome-do-profile/
  → configura provider, schema, etc.

celebro sync
  → para cada profile, brain_tool recall para pegar conhecimento relevante
  → brain_tool remember para salvar no global (se desejado)

celebro backup
  → backup.sh ou equivalente, para global + todos os profiles

celebro update
  → git pull origin main (ou download do release)
  → reinstala se necessário
```

---

## Comandos do Celebro

### `sudo celebro update`

Atualiza o framework para a versão mais recente.

```bash
sudo celebro update
```

O que faz:
1. Verifica a versão atual (`celebro version`)
2. Conecta ao repo (GitHub ou onde estiver hospedado)
3. Pega a versão mais recente
4. Substitui os arquivos locais
5. Reporta o que mudou

### `sudo celebro add profile`

Adiciona um novo profile ao sistema.

```bash
sudo celebro add profile nome-do-profile
```

O que faz:
1. Pergunta ao usuário: qual provider usar? (Nous, Ollama, etc.)
2. Pergunta: qual LLM/default usar? (free LLMs por padrão)
3. Cria ~/.brain/profiles/nome-do-profile/
4. Cria brain.db com schema
5. Cria schema_pack.yaml
6. Configura o profile para usar o provider escolhido
7. Reporta o que foi criado

### `sudo celebro sync`

Sincroniza brains entre profiles.

```bash
sudo celebro sync
```

O que faz:
1. Para cada profile, lista o conhecimento relevante
2. Pergunta ao usuário: o que sincronizar? (global, specific, tudo)
3. Executa a sincronização via brain_tool
4. Reporta o que foi sincronizado

### `sudo celebro backup`

Backup de todos os brains.

```bash
sudo celebro backup
```

O que faz:
1. Backup do brain global
2. Backup de cada profile
3. Guarda em ~/.brain/backups/
4. Rotação: mantém últimos N backups
5. Reporta o que foi backupado

---

## Universal/Genérico

Celebro é universal e genérico — não assume nada sobre o sistema do usuário.

- **Não hardcode nomes de profiles** (Granjimmy, gtic, jimmy, etc.)
- **Não assumir que há um "sistema principal"** — Celebro é neutro
- **Não assumir caminhos específicos** — perguntar ao usuário ou usar padrões configuráveis
- **Não assumir provider específico** — suportar vários, deixar escolha ao usuário

---

## Exemplo de uso

### Adicionar um profile

```bash
# Adiciona um profile chamado "meu-sistema"
sudo celebro add profile meu-sistema

# O Celebro pergunta:
# - Qual provider usar? [Nous] 
# - Qual LLM/default usar? [gemma4:31b ou outro free]
# - Caminho do brain? [~/.brain/profiles/meu-sistema]

# Celebro cria:
# ~/.brain/profiles/meu-sistema/
#   ├── brain.db
#   ├── schema_pack.yaml
#   └── config.yaml (com provider, LLM, etc.)
```

### Sincronizar

```bash
# Sincroniza todos os profiles com o global
sudo celebro sync

# O Celebro pergunta:
# - O que sincronizar? [tudo] 
# - Para onde? [global]
```

### Backup

```bash
# Backup de todos os brains
sudo celebro backup

# O Celebro cria:
# ~/.brain/backups/
#   ├── global_brain_20260814_103000.db
#   └── profiles/
#       ├── meu-sistema_brain_20260814_103000.db
#       └── outro-profile_brain_20260814_103000.db
```

---

## Diferença entre Celebro e brain_tool

| | Celebro | brain_tool |
|---|---|---|
| **O que é** | Perfil mestre nativo do framework | CLI para manipular brain.db |
| **Para quem** | Usuários do sistema | Qualquer um que queira usar um brain.db |
| **O que faz** | Gerencia instalação, profiles, sync, backup | CRUD de conhecimento, schema, etc. |
| **Como usar** | `sudo celebro <command>` | `python3 -m brain_tool <command>` |
| **Universal?** | Sim — não assume nada sobre o sistema | Sim — agnóstico a qualquer sistema |

Celebro usa brain_tool nos bastidores — não reimplementa a manipulação de brain.db.
