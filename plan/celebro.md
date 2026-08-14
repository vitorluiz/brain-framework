# Celebro — Plano de Desenvolvimento

> Celebro é o **celebro inteligente do Hermes Agent** — perfil mestre nativo do brain-framework.
> Este plano define o que é, como funciona, e o que precisa ser implementado.

---

## 1. Visão geral

**Celebro** é o celebro inteligente do Hermes Agent — perfil mestre nativo do
brain-framework. Ele é o gestor do sistema — não um agente comum, mas a camada
que gerencia instalação, profiles, sincronização, e backup, **integrado com o
próprio Hermes Agent**.

Celebro usa o CLI do brain (brain_tool.py) como base para manipular brain.db, e
releva os comandos nativos do Hermes Agent para gerir profiles:

- `sudo celebro update` — atualiza o framework
- `sudo celebro add profile` — adiciona um novo profile (cria profile Hermes + alias + brain.db)
- `sudo celebro sync` — sincroniza brains entre profiles
- `sudo celebro backup` — backup de todos os brains

---

## 2. Celebro como celebro inteligente do Hermes Agent

### 2.1 Natureza

- **Nativo do framework** — vem com a instalação, não é um "profile de fora"
- **Celebro inteligente do Hermes Agent** — ele sabe como criar profiles Hermes,
  configurar aliases, etc. (não um gestor externo)
- **Universal** — não assume nada sobre o sistema do usuário

### 2.2 Como Celebro gerencia um profile

Quando o usuário executa `sudo celebro add profile <name>`, o Celebro:

1. **Cria o profile Hermes** via comando nativo:
   ```
   hermes profile create <name>
   ```
   Isso cria toda a estrutura do profile no diretório de profiles do Hermes.

2. **Configura o alias** para o usuário power usar o profile:
   ```
   printf '\nalias <name>="/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile <name>"\n' >> ~/.bashrc
   source ~/.bashrc
   ```
   Exemplo para profile "marketing":
   ```
   printf '\nalias marketing="/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile marketing"\n' >> ~/.bashrc
   source ~/.bashrc
   ```

3. **Cria o brain.db** do novo profile:
   ```
   brain_tool init --scope <name> --brain-dir ~/.brain/profiles/<name>/
   ```

4. **Configura provider, LLM, etc.** — pergunta ao usuário ou usa defaults.

---

## 3. Commands do Celebro

### 3.1 `sudo celebro update`

Atualiza o framework para a versão mais recente.

```bash
# Uso
sudo celebro update

# O que faz:
# 1. Verifica a versão atual (`celebro version`)
# 2. Conecta ao repo (GitHub ou onde estiver hospedado)
# 3. Pega a versão mais recente
# 4. Substitui os arquivos locais
# 5. Reporta o que mudou
```

### 3.2 `sudo celebro add profile`

Adiciona um novo profile ao sistema.

```bash
# Uso
sudo celebro add profile <name>

# O que faz:
# 1. Cria o profile Hermes: hermes profile create <name>
# 2. Configura o alias: printf '\nalias <name>="..."' >> ~/.bashrc && source ~/.bashrc
# 3. Cria o brain.db: brain_tool init --scope <name> --brain-dir ~/.brain/profiles/<name>/
# 4. Pergunta ao usuário: qual provider usar? (Nous, Ollama, etc.)
# 5. Pergunta: qual LLM/default usar? (free LLMs por padrão)
# 6. Configura o profile para usar o provider escolhido
# 7. Reporta o que foi criado
```

### 3.3 `sudo celebro sync`

Sincroniza brains entre profiles.

```bash
# Uso
sudo celebro sync

# O que faz:
# 1. Para cada profile, lista o conhecimento relevante
# 2. Pergunta ao usuário: o que sincronizar? (global, specific, tudo)
# 3. Executa a sincronização via brain_tool
# 4. Reporta o que foi sincronizado
```

### 3.4 `sudo celebro backup`

Backup de todos os brains.

```bash
# Uso
sudo celebro backup

# O que faz:
# 1. Backup do brain global
# 2. Backup de cada profile
# 3. Guarda em ~/.brain/backups/
# 4. Rotação: mantém últimos N backups
# 5. Reporta o que foi backupado
```

---

## 4. Celebro como ferramenta de linha de comando

Celebro é instalado como um comando de sistema:

```bash
# Após instalar o framework
sudo celebro --version
sudo celebro update
sudo celebro add profile marketing
sudo celebro sync
sudo celebro backup
```

A instalação do celebro é feita pelo `pyproject.toml` do framework — ele registered
como um comando de sistema (via `console_scripts` ou similar).

**Exemplo de uso completo (adicionar profile marketing):**

```bash
# 1. Adicionar profile
sudo celebro add profile marketing

# O Celebro executa:
# - hermes profile create marketing
# - printf '\nalias marketing="/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile marketing"\n' >> ~/.bashrc
# - source ~/.bashrc
# - brain_tool init --scope marketing --brain-dir ~/.brain/profiles/marketing/
# - Pergunta: provider? (Nous, Ollama, etc.) — default: Nous
# - Pergunta: LLM/default? (free LLMs) — default: gemma4:31b ou outro free

# 2. Usar o profile (agora com alias)
marketing --help
# ou
marketing <comando>

# 3. Sincronizar
sudo celebro sync

# 4. Backup
sudo celebro backup
```

---

## 5. Universal/Genérico — o que NÃO fazer

- **Não hardcode nomes de profiles** (Granjimmy, gtic, jimmy, etc.)
- **Não assumir que há um "sistema principal"** — Celebro é neutro
- **Não assumir caminhos específicos** — perguntar ao usuário ou usar padrões configuráveis
- **Não assumir provider específico** — suportar vários, deixar escolha ao usuário

---

## 6. O que já existe (reuso)

- `brain_tool.py` — manipulação de brain.db, schema, comandos CRUD
- `backup.sh` — backup/restore (pode ser adaptado para celebro backup)
- `schema_pack.yaml` — schema de taxonomia (pode ser usado como template)
- Estrutura de diretórios (global, experts) — convenção, não obrigatoriedade
- Comando `hermes profile create <name>` — cria profiles Hermes
- Alias padrão do Hermes Agent para profiles (exemplo: `marketing`)

---

## 7. O que precisa ser implementado

### 7.1 Celebro CLI

- [ ] `celebro/cli.py` — entry point para `sudo celebro`
- [ ] `celebro/core.py` — lógica de gestão (add profile, sync, backup, update)
- [ ] `celebro/config.py` — configuração do Celebro (provider, LLMs, paths)

### 7.2 Integração com Hermes Agent

- [ ] Celebro chama `hermes profile create <name>` para criar profiles
- [ ] Celebro configura alias no ~/.bashrc (exemplo: `marketing`)
- [ ] Celebro chama `brain_tool init` para criar brain.db do novo profile

### 7.3 Integração com brain_tool

- [ ] Celebro chama `brain_tool` nos bastidores (subprocess ou import)
- [ ] Comandos do Celebro mapeados para operações do brain_tool

### 7.4 Instalação

- [ ] `pyproject.toml` registra celebro como console_script
- [ ] `pip install` instala celebro como comando de sistema

### 7.5 Configuração

- [ ] Arquivo de configuração do Celebro (~/.brain/celebro.yaml ou similar)
- [ ] Provider default, LLMs free, paths configuráveis

---

## 8. Critério de aceitação

- [ ] `sudo celebro --version` funciona e reporta a versão
- [ ] `sudo celebro update` atualiza o framework
- [ ] `sudo celebro add profile marketing`:
  - Cria profile Hermes via `hermes profile create marketing`
  - Configura alias `marketing` no ~/.bashrc
  - Cria brain.db via `brain_tool init`
  - Configura provider/LLM
  - Reporta o que foi criado
- [ ] `sudo celebro sync` sincroniza brains entre profiles
- [ ] `sudo celebro backup` backupa todos os brains
- [ ] Celebro é universal — não assume nada sobre o sistema do usuário
- [ ] Celebro usa LLMs free por padrão (configurável)

---

## 9. Próximos passos

1. **Definir estrutura de arquivos** do celebro no projeto
2. **Implementar celebro/cli.py** — entry point
3. **Implementar celebro/core.py** — lógica de gestão
4. **Implementar celebro/config.py** — configuração
5. **Integrar com Hermes Agent** — chamar `hermes profile create` e configurar alias
6. **Integrar com brain_tool** — chamar nos bastidores
7. **Testar com um profile de exemplo** (não usar profiles existentes da Granjimmy)
8. **Documentar** em doc/
