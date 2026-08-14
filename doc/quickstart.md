# Quickstart — Brain Framework

> Primeiro uso do Brain Framework em 5 minutos.
> Este guia assume que você já instalou o framework.

---

## Instalação (se ainda não instalou)

```bash
# Instalar direto do GitHub
pip install git+ssh://git@github.com/vitorluiz/brain-framework.git

# Ou clonar e instalar localmente
git clone git@github.com:vitorluiz/brain-framework.git
cd brain-framework
pip install -e .
```

---

## Verificar a instalação

```bash
# Verificar a versão do framework
python3 -m brain_tool --version

# Verificar a versão do Celebro
sudo celebro --version
```

---

## Primeiro uso com brain_tool

### 1. Criar um brain

```bash
# Criar um brain para o seu sistema
python3 -m brain_tool init --scope meu-sistema --brain-dir ~/.meu_sistema/brain/
```

Isso cria:
- `~/.meu_sistema/brain/brain.db` — base SQLite com o schema de conhecimento
- `~/.meu_sistema/brain/schema_pack.yaml` — definição dos tipos/taxonomia usados

### 2. Adicionar conhecimento

```bash
# Salvar um conceito
python3 -m brain_tool --brain ~/.meu_sistema/brain/ remember \
  --tipo concepts \
  --titulo "Meu conceito" \
  --corpo "Descrição do meu conceito"

# Salvar uma entidade
python3 -m brain_tool --brain ~/.meu_sistema/brain/ entity \
  --nome "João" \
  --descricao "Responsável pelo sistema"
```

### 3. Recuperar conhecimento

```bash
# Listar todos os conceitos
python3 -m brain_tool --brain ~/.meu_sistema/brain/ recall --tipo concepts

# Buscar por termo
python3 -m brain_tool --brain ~/.meu_sistema/brain/ recall --tipo concepts --termo "conceito"
```

---

## Primeiro uso com Celebro

### 1. Adicionar um profile

```bash
# Adicionar um profile para o seu sistema
sudo celebro add profile meu-sistema

# O Celebro pergunta:
# - Qual provider usar? [Nous] 
# - Qual LLM/default usar? [gemma4:31b ou outro free]
# - Caminho do brain? [~/.brain/profiles/meu-sistema]
```

### 2. Usar o profile

```bash
# Agora você pode usar o brain_tool com o profile
python3 -m brain_tool --brain ~/.brain/profiles/meu-sistema/ recall --tipo concepts
```

### 3. Sincronizar

```bash
# Sincronizar todos os profiles com o global
sudo celebro sync
```

### 4. Backup

```bash
# Backup de todos os brains
sudo celebro backup
```

---

## Estrutura de diretórios esperada

```
~/.brain/                    # raiz dos brains do usuário
├── global/
│   ├── brain.db             # knowledge base compartilhada
│   └── schema_pack.yaml
├── profiles/
│   ├── meu-sistema/
│   │   ├── brain.db
│   │   └── schema_pack.yaml
│   └── outro-profile/
│       ├── brain.db
│       └── schema_pack.yaml
└── backups/
    ├── global/
    │   └── brain_YYYYMMDD_HHMMSS.db
    └── profiles/
        └── meu-sistema/
            └── brain_YYYYMMDD_HHMMSS.db
```

---

## Próximos passos

- Leia a [documentação de uso](./README.md) para entender os conceitos
- Leia a [referência de comandos](./commands.md) para saber todos os comandos disponíveis
- Planeje seu desenvolvimento no [plan/](../plan/)
