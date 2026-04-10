# Dashboard de Saúde — Taques Agents

Sistema de observabilidade para MCPs e Skills do Claude Code.

## Acesso rápido

```bash
# Subir servidor (se não estiver rodando)
cd '/Users/lenontaques/Documents/Obsidian do Lenon/3-recursos/agent-dashboard' && python3 -m http.server 8084 &

# Abrir no browser
open http://localhost:8084/saude.html

# Verificar se está no ar
curl -s -o /dev/null -w "%{http_code}" http://localhost:8084/saude.html
```

## Arquivos do sistema

| Arquivo | Função |
|---------|--------|
| `saude.html` | Dashboard visual — abre no browser |
| `tool-logger.py` | Captura tool calls via hook → atualiza tool-status.json |
| `tool-status.json` | Estado agregado por MCP/Skill (não editar manualmente) |
| `index.html` | Dashboard principal de orquestração (porta 8083) |
| `registry.json` | Registro de skills/agentes (gerado por sync-registry.py) |
| `sync-registry.py` | Gera registry.json a partir dos SKILL.md |

## Como o logger funciona

O hook `PostToolUse` dispara `tool-logger.py` após cada chamada de MCP ou Skill.
O script lê JSON do stdin (formato do Claude Code) e atualiza `tool-status.json`.

```
Claude Code → tool call → PostToolUse hook → tool-logger.py → tool-status.json → saude.html
```

## Ativar o hook (se ainda não estiver ativo)

```bash
python3 -c "
import json
path = '/Users/lenontaques/.claude/settings.json'
d = json.load(open(path))
novo = {
  'matcher': 'mcp__.*|Skill',
  'hooks': [{'type':'command','command':\"python3 '/Users/lenontaques/Documents/Obsidian do Lenon/3-recursos/agent-dashboard/tool-logger.py'\",'timeout':5,'async':True}]
}
if not any(h.get('matcher')=='mcp__.*|Skill' for h in d['hooks']['PostToolUse']):
    d['hooks']['PostToolUse'].insert(0, novo)
    json.dump(d, open(path,'w'), indent=2, ensure_ascii=False)
    print('Hook adicionado.')
else:
    print('Hook já existe.')
"
```

## Adicionar novo MCP ao dashboard

1. Abrir `tool-logger.py` e adicionar o servidor em `MCP_LABELS`
2. Abrir `tool-status.json` e adicionar entrada na seção `mcps`
3. Abrir `saude.html` e adicionar descrição em `DESCRIPTIONS`

## Adicionar nova Skill ao dashboard

1. Abrir `tool-status.json` e adicionar entrada na seção `skills`
2. Abrir `saude.html` e adicionar descrição em `DESCRIPTIONS`

## Status das cores

| Cor | Significado |
|-----|------------|
| Verde | Última chamada foi bem-sucedida |
| Vermelho | Última chamada retornou erro |
| Cinza | Nunca foi usado nesta instalação |

## Criado em

2026-04-09 — Sessão de estudo sobre AI Observability / LLMOps
Ver: [[2-areas/sessoes-claude/sessao-2026-04-09-observabilidade-ia-dashboard-saude]]
