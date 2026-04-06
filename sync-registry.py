#!/usr/bin/env python3
"""
sync-registry.py — Sincroniza skills/agentes com registry.json + Supabase.

Fontes de dados:
  1. .claude/skills/*/SKILL.md → frontmatter YAML → habilidades
  2. Dados estáticos de agentes e automações (inline)

Destinos:
  1. registry.json (para dashboard estático v1)
  2. Supabase (para dashboard Next.js v2)

Executado automaticamente via hook do Claude Code após edição em .claude/skills/.
Também pode ser rodado manualmente: python3 sync-registry.py
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# Paths relativos ao vault do Obsidian
VAULT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = VAULT / ".claude" / "skills"
OUTPUT = Path(__file__).resolve().parent / "registry.json"

# Supabase config
SUPABASE_URL = "https://futnqzaefgoljrhjfusj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ1dG5xemFlZmdvbGpyaGpmdXNqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ4MjIyOTUsImV4cCI6MjA5MDM5ODI5NX0.4GcUgEtVsRSyuO7-EIlqLQKkOwiGZikBRP4kB2vLCmk"


def parse_yaml_frontmatter(filepath):
    """Extrai frontmatter YAML de um arquivo .md (entre --- e ---)."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return {}

    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}

    fm = {}
    raw = match.group(1)

    current_key = None
    current_val = ""
    for line in raw.split("\n"):
        if current_key and (line.startswith("  ") or line.strip() == ""):
            current_val += " " + line.strip()
            continue

        if current_key:
            fm[current_key] = current_val.strip()
            current_key = None
            current_val = ""

        kv = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if kv:
            key = kv.group(1)
            val = kv.group(2).strip()

            if val == ">":
                current_key = key
                current_val = ""
                continue

            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]

            fm[key] = val

    if current_key:
        fm[current_key] = current_val.strip()

    return fm


def split_description(full_desc):
    for separator in ["Use SEMPRE", "Acionar quando", "TRIGGER when", "Sempre que"]:
        idx = full_desc.find(separator)
        if idx > 0:
            return full_desc[:idx].strip().rstrip("."), full_desc[idx:]
    return full_desc.strip(), ""


def extract_tags_from_skill(fm):
    tag_map = {
        "assessor-comunicacao": ["Slack", "WhatsApp", "Email", "Trello", "Instagram"],
        "redacao-juridica": ["CRARC", "Teses Amb.", "Teses Penais", ".docx"],
        "separador-de-pdfs": ["Divisão PDF", "Cronológico", "NotebookLM", "Índice.md"],
        "digitalizador-documentos": ["Escâner", "150 DPI", "Auto/Cor/PB", "Multi-página"],
        "extrator-car": ["CAR/SICAR", "Shapefiles", "GeoPandas", "Alertas", "Lote"],
        "extrator-eproc": ["E-PROC", "TJSC", "Playwright", "Cert. A1", "Lote"],
    }
    name = fm.get("name", "")
    if name in tag_map:
        return tag_map[name]

    # Fallback: parse tags field from frontmatter
    tags_raw = fm.get("tags", "")
    if tags_raw.startswith("[") and tags_raw.endswith("]"):
        return [t.strip().strip("'\"") for t in tags_raw[1:-1].split(",") if t.strip()]

    return []


def extract_skill_body_info(filepath):
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return {}

    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    info = {}
    passos = re.findall(r"^## PASSO \d", body, re.MULTILINE)
    if passos:
        info["passos"] = len(passos)
    if "```python" in body:
        info["tem_codigo"] = True
    return info


def infer_status(fm, body_info):
    desc = fm.get("description", "").lower()
    name = fm.get("name", "").lower()
    status_overrides = {"extrator-car": "prototype", "extrator-eproc": "prototype"}
    if name in status_overrides:
        return status_overrides[name]
    if "em construção" in desc or "em construcao" in desc:
        return "prototype"
    if "planejado" in desc or "planned" in desc:
        return "planned"
    return "active"


def infer_icon(name):
    icons = {
        "assessor-comunicacao": "💬", "redacao-juridica": "⚖",
        "separador-de-pdfs": "📄", "digitalizador-documentos": "📷",
        "extrator-car": "🌍", "extrator-eproc": "🏛",
    }
    return icons.get(name, "⚙")


def infer_department(fm):
    desc = fm.get("description", "").lower()
    if any(k in desc for k in ["técnico", "tecnico", "car", "sicar", "ambiental", "incra"]):
        return "tecnico"
    if any(k in desc for k in ["jurídico", "juridico", "petição", "peticao", "processual"]):
        return "juridico"
    return "geral"


def infer_type_label(fm):
    name = fm.get("name", "")
    type_map = {
        "assessor-comunicacao": "Habilidade · Agente de Comunicação",
        "redacao-juridica": "Habilidade · Agente de Escrita Legal",
        "separador-de-pdfs": "Habilidade · Agente de Processamento Documental",
        "digitalizador-documentos": "Habilidade · Agente de Processamento de Imagem",
        "extrator-car": "Habilidade · Dept. Técnico — Estagiário Técnico",
        "extrator-eproc": "Habilidade · Dept. Jurídico — Estagiário Jurídico",
    }
    return type_map.get(name, "Habilidade · Geral")


def scan_skills():
    skills = []
    if not SKILLS_DIR.exists():
        return skills

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        fm = parse_yaml_frontmatter(skill_md)
        if not fm.get("name"):
            continue

        body_info = extract_skill_body_info(skill_md)
        full_desc = fm.get("description", "")
        desc_short, _ = split_description(full_desc)
        if len(desc_short) > 200:
            desc_short = desc_short[:200] + "..."

        name_map = {
            "assessor-comunicacao": "Assessor de Comunicação",
            "redacao-juridica": "Redação Jurídica",
            "separador-de-pdfs": "Separador de PDFs",
            "digitalizador-documentos": "Digitalizador de Documentos",
            "extrator-car": "Extrator CAR (SICAR)",
            "extrator-eproc": "Extrator E-PROC (TJSC)",
        }
        display_name = name_map.get(fm["name"], fm["name"].replace("-", " ").title())

        skill = {
            "id": fm["name"],
            "name": display_name,
            "type_label": infer_type_label(fm),
            "description": desc_short,
            "tags": extract_tags_from_skill(fm),
            "status": infer_status(fm, body_info),
            "icon": infer_icon(fm["name"]),
            "path": f".claude/skills/{skill_dir.name}/SKILL.md",
            "department": infer_department(fm),
            "passos": body_info.get("passos", 0),
            "tem_codigo": body_info.get("tem_codigo", False),
        }
        skills.append(skill)

    return skills


def get_agents():
    return {
        "juridico": [
            {"id": "estagiario-juridico", "name": "Agente 0 — Estagiário Jurídico", "role": "Extração e Coleta", "description": "Extrai autos de processos dos sistemas judiciais.", "tags": ["EPROC", "SGPe", "PROJUDI", "ESAJ"], "status": "prototype", "icon": "🎓", "department": "juridico", "level": 0},
            {"id": "advogado-junior", "name": "Agente 1 — Advogado Júnior", "role": "Análise e Separação", "description": "Separa e classifica peças processuais.", "tags": ["Proc. Admin.", "EPROC", "PROJUDI"], "status": "planned", "icon": "🔍", "department": "juridico", "level": 1},
            {"id": "advogado-pleno", "name": "Agente 2 — Advogado Pleno", "role": "Redação e Argumentação", "description": "Redige peças completas.", "tags": ["Fatos", "Fundamentos", "Pedidos", "Resumo"], "status": "planned", "icon": "✍", "department": "juridico", "level": 2, "sub_agents": [
                {"id": "pesquisador-juris-dominante", "name": "Pesquisador de Jurisprudência Dominante", "role": "Analista de tendências majoritárias", "description": "Mapeia jurisprudência consolidada nos tribunais.", "tags": ["jurisprudência", "tendências", "súmulas", "entendimento-majoritário"], "status": "planned", "icon": "⚖️", "department": "juridico", "level": 3},
                {"id": "pesquisador-juris-vanguarda", "name": "Pesquisador de Jurisprudência de Vanguarda", "role": "Analista de tendências emergentes", "description": "Identifica decisões inovadoras e divergentes.", "tags": ["jurisprudência", "divergência", "inovação", "tendências-emergentes"], "status": "planned", "icon": "🔭", "department": "juridico", "level": 3},
                {"id": "analista-jurimetria", "name": "Analista de Jurimetria", "role": "Analista quantitativo", "description": "Análise estatística de decisões por tribunal.", "tags": ["jurimetria", "estatística", "análise-quantitativa", "dados"], "status": "planned", "icon": "📊", "department": "juridico", "level": 3},
                {"id": "pesquisador-jurisprudencia", "name": "Compilador de Jurisprudência", "role": "Pesquisador de decisões específicas", "description": "Busca decisões concretas conforme plano aprovado.", "tags": ["jurisprudência", "compilação", "busca-decisões", "TJ", "TRF", "STJ", "STF"], "status": "planned", "icon": "📚", "department": "juridico", "level": 3},
            ]},
            {"id": "advogado-senior", "name": "Agente 3 — Advogado Sênior", "role": "Estratégia e Jurisprudência", "description": "Estratégia processual e jurisprudência.", "tags": ["Estratégia", "Juris. Dominante", "Juris. Vanguarda"], "status": "planned", "icon": "🎯", "department": "juridico", "level": 3},
        ],
        "tecnico": [
            {"id": "estagiario-tecnico", "name": "Agente 0 — Estagiário Técnico", "role": "Extração de Dados Ambientais", "description": "Coleta dados das bases ambientais e fundiárias.", "tags": ["CAR", "INCRA", "MapBiomas", "IMA-SC", "IBAMA"], "status": "prototype", "icon": "🧪", "department": "tecnico", "level": 0},
            {"id": "analista-junior", "name": "Agente 1 — Analista MA Júnior", "role": "Organização e Cruzamento", "description": "Tabula dados extraídos, cruza matrícula × CAR × INCRA.", "tags": ["Tabulação", "Matrícula×CAR", "Matrícula×INCRA", "Shapefiles"], "status": "planned", "icon": "📊", "department": "tecnico", "level": 1},
            {"id": "analista-pleno", "name": "Agente 2 — Analista MA Pleno", "role": "Análise Integrada e Relatórios", "description": "Análise de APP e Reserva Legal, mapas temáticos.", "tags": ["APP", "Reserva Legal", "Mapas", "Relatórios"], "status": "planned", "icon": "🗺", "department": "tecnico", "level": 2},
            {"id": "analista-senior", "name": "Agente 3 — Analista MA Sênior", "role": "Diagnóstico Estratégico", "description": "Score de risco ambiental, consolidação de passivos.", "tags": ["Score Risco", "Passivos", "Ranking", "Estratégia"], "status": "planned", "icon": "🚨", "department": "tecnico", "level": 3},
        ],
        "administrativo": [
            {"id": "controlador-juridico", "name": "Controlador Jurídico", "role": "Controle e Distribuição de Prazos", "description": "Anota, acompanha e distribui prazos judiciais e extrajudiciais. Cria tarefas no Trello.", "tags": ["Prazos", "Trello", "Judicial", "Extrajudicial", "Distribuição"], "status": "planned", "icon": "📋", "department": "administrativo", "level": 0},
        ],
        "comercial": [
            {"id": "coordenador-comercial", "name": "Coordenador Comercial", "role": "Prospecção e Gestão Comercial", "description": "Coordena o pipeline comercial do escritório. Acompanha novos negócios, propostas e conversões.", "tags": ["Pipeline", "Propostas", "Follow-up", "Leads", "Conversão"], "status": "planned", "icon": "💼", "department": "comercial", "level": 0},
        ],
        "geral": [
            {"id": "verificador-compromissos", "name": "Verificador de Compromissos", "role": "Verificação diária de compromissos pessoais via WhatsApp + GCal", "description": "Agente automatizado que verifica diariamente compromissos pessoais recorrentes (terapia, barbearia) cruzando Google Calendar com WhatsApp e reportando ao painel.", "tags": ["WhatsApp", "Google Calendar", "Todoist", "Supabase", "Cron", "Pessoal"], "status": "active", "icon": "🔍", "department": "geral", "level": 0},
        ],
        "gabinete-ceo": [
            {"id": "secretaria-executiva", "name": "Agente 1 — Secretária Executiva", "role": "Despacho de Trello e Triagem de Notificações", "description": "Processa notificações do Trello, enriquece contexto com 7 fontes externas (Supabase, Gmail, Drive, Calendar, Slack, Todoist, WhatsApp), sugere respostas e ações, e documenta no Obsidian. Human-in-the-Loop obrigatório.", "tags": ["Trello", "Gmail", "Calendar", "Todoist", "WhatsApp", "Slack", "Drive", "Supabase", "Obsidian", "HITL"], "status": "prototype", "icon": "🗂", "department": "gabinete-ceo", "level": 1},
        ],
    }


def get_automations():
    return [
        {"id": "monitor-iat", "name": "Monitor de Protocolos IAT", "type_label": "Automação · Cron + Playwright + Supabase", "description": "Monitora 38+ protocolos no IAT.", "tags": ["Playwright", "Supabase", "Cron", "Slack"], "status": "prototype", "icon": "🔔"},
        {"id": "doc-obsidian", "name": "Documentação Automática Obsidian", "type_label": "Gatilho · Documentação de Sessão", "description": "Documenta sessões e decisões no Obsidian.", "tags": ["Gatilhos", "Obsidian", "Sessões"], "status": "active", "icon": "📝"},
        {"id": "briefing-comercial", "name": "Briefing Comercial", "type_label": "Automação · Cron · Coordenador Comercial", "description": "Scan diagnóstico dos boards Novos Negócios (TAQUES + DF Bio). Identifica leads parados, sem responsável, sem prazo e vencidos. Gera relatório para aprovação.", "tags": ["Novos Negócios", "Trello", "Diagnóstico", "6h"], "status": "planned", "icon": "📊", "agent_id": "coordenador-comercial", "trigger_type": "cron", "trigger_config": {"cron": "0 6 * * 1-5", "boards": ["698e0e318ec5969402a1b2a5", "698e0d2afa67614e223e358e"]}},
        {"id": "check-comercial", "name": "Check de Execução Comercial", "type_label": "Automação · Cron · Coordenador Comercial", "description": "Verificação de progresso nos boards Novos Negócios. Compara com briefing da manhã, identifica movimentações e follow-ups pendentes. Propõe ações.", "tags": ["Novos Negócios", "Trello", "Follow-up", "15h/18h"], "status": "planned", "icon": "🔄", "agent_id": "coordenador-comercial", "trigger_type": "cron", "trigger_config": {"cron": "0 15,18 * * 1-5", "boards": ["698e0e318ec5969402a1b2a5", "698e0d2afa67614e223e358e"]}},
        {"id": "verificador-compromissos", "name": "Verificador de Compromissos Pessoais", "type_label": "Automação · Cron · WhatsApp + GCal", "description": "Verifica compromissos pessoais recorrentes (terapia, barbearia) via WhatsApp e Google Calendar. Confirma se mensagens de confirmação foram enviadas e reporta ao painel.", "tags": ["WhatsApp", "Google Calendar", "Todoist", "Pessoal", "6h"], "status": "active", "icon": "🔍", "agent_id": "verificador-compromissos", "trigger_type": "cron", "trigger_config": {"cron": "0 6 * * *", "compromissos": [{"tipo": "terapia", "contato": "Bruna de Oliveira", "jid": "554188887092@s.whatsapp.net"}, {"tipo": "corte-cabelo", "contato": "Bravos Barbearia", "jid": "554797035509@s.whatsapp.net"}]}},
        {"id": "reordenar-rituais-todoist", "name": "Reordenação de Rituais Todoist", "type_label": "Automação · Cron · Vercel + Todoist API", "description": "Reordena subtarefas dos rituais Matinal e Noturno no Todoist diariamente à 00:01. Corrige bug de reordenação ao completar tarefas recorrentes. Roda via Vercel Cron independente do Claude Code.", "tags": ["Todoist", "Vercel Cron", "Rituais", "00:01"], "status": "active", "icon": "🔄", "trigger_type": "cron", "trigger_config": {"cron": "1 0 * * *", "endpoint": "/api/cron/reordenar-rituais", "matinal_tasks": 9, "noturno_tasks": 9}},
    ]


# ── MCP Servers ─────────────────────────────────────────────────────────────

MCP_REGISTRY = [
    # Stdio MCPs (definidos em ~/.claude/.mcp.json)
    {"id": "todoist", "name": "Todoist", "url": "mcp://todoist", "detect": "stdio", "config_key": "todoist",
     "tools": ["add-tasks", "find-tasks", "update-tasks", "complete-tasks", "get-overview", "reschedule-tasks"]},
    {"id": "trello", "name": "Trello", "url": "mcp://trello", "detect": "stdio", "config_key": "trello",
     "tools": ["getMyBoards", "getLists", "getCardsByList", "getMyCards", "addCard", "updateCard", "moveCard", "archiveCard"]},
    {"id": "google-drive", "name": "Google Drive", "url": "mcp://gdrive", "detect": "stdio", "config_key": "google-drive",
     "tools": ["google_drive_search", "google_drive_fetch"]},
    # SSE MCPs (processo separado)
    {"id": "whatsapp", "name": "WhatsApp (GOWA)", "url": "http://localhost:8080", "detect": "sse",
     "health_url": "http://localhost:8080",
     "tools": ["whatsapp_send_text", "whatsapp_list_chats", "whatsapp_get_chat_messages", "whatsapp_send_image"]},
    # Plugin MCPs (marketplace)
    {"id": "slack", "name": "Slack", "url": "mcp://slack", "detect": "plugin", "plugin_name": "slack",
     "tools": ["slack_send_message", "slack_read_channel", "slack_search_public", "slack_create_canvas"]},
    {"id": "supabase", "name": "Supabase", "url": "mcp://supabase", "detect": "plugin", "plugin_name": "supabase",
     "tools": ["execute_sql", "apply_migration", "list_tables", "list_projects", "get_project"]},
    # Conectores gerenciados (infraestrutura Claude)
    {"id": "google-calendar", "name": "Google Calendar", "url": "mcp://gcal", "detect": "connector",
     "tools": ["gcal_list_events", "gcal_create_event", "gcal_update_event", "gcal_find_free_time"]},
    {"id": "gmail", "name": "Gmail", "url": "mcp://gmail", "detect": "connector",
     "tools": ["gmail_search_messages", "gmail_read_message", "gmail_create_draft"]},
]


def _check_mcp_status(mcp, stdio_config, plugins_dir):
    """Determina status de conexão de um MCP server."""
    detect = mcp.get("detect", "unknown")

    if detect == "stdio":
        config_key = mcp.get("config_key", "")
        if config_key not in stdio_config:
            return "disconnected"
        cmd = stdio_config[config_key].get("command", "")
        if not cmd:
            return "disconnected"
        if cmd in ("npx", "node"):
            return "connected"
        return "connected" if Path(cmd).exists() else "disconnected"

    elif detect == "sse":
        health_url = mcp.get("health_url", "")
        if not health_url:
            return "unknown"
        try:
            req = urllib.request.Request(health_url, method="HEAD")
            urllib.request.urlopen(req, timeout=3)
            return "connected"
        except urllib.error.HTTPError:
            return "connected"  # servidor respondeu, está vivo
        except Exception:
            return "disconnected"

    elif detect == "plugin":
        plugin_name = mcp.get("plugin_name", "")
        if plugin_name and (plugins_dir / plugin_name).exists():
            return "connected"
        return "disconnected"

    elif detect == "connector":
        return "connected"

    return "unknown"


def get_mcps():
    """Escaneia e verifica todos os MCP servers conhecidos."""
    mcp_json_path = Path.home() / ".claude" / ".mcp.json"
    stdio_config = {}
    if mcp_json_path.exists():
        try:
            stdio_config = json.loads(mcp_json_path.read_text()).get("mcpServers", {})
        except Exception:
            pass

    plugins_dir = (
        Path.home() / ".claude" / "plugins" / "marketplaces"
        / "claude-plugins-official" / "external_plugins"
    )

    results = []
    for mcp in MCP_REGISTRY:
        status = _check_mcp_status(mcp, stdio_config, plugins_dir)
        results.append({
            "id": mcp["id"],
            "name": mcp["name"],
            "url": mcp["url"],
            "status": status,
            "tools_available": mcp["tools"],
        })
    return results


def build_registry():
    skills = scan_skills()
    agents = get_agents()
    automations = get_automations()
    mcps = get_mcps()

    active_skills = len([s for s in skills if s["status"] == "active"])
    prototype_skills = len([s for s in skills if s["status"] == "prototype"])
    total_agents = sum(len(v) for v in agents.values())
    mcps_connected = len([m for m in mcps if m["status"] == "connected"])

    return {
        "_meta": {"generated_at": datetime.now().isoformat(), "generator": "sync-registry.py", "vault": str(VAULT)},
        "stats": {
            "habilidades_ativas": active_skills, "habilidades_construcao": prototype_skills,
            "habilidades_total": len(skills), "agentes_total": total_agents,
            "automacoes_total": len(automations), "mcps_total": len(mcps),
            "mcps_connected": mcps_connected,
        },
        "skills": skills,
        "agents": agents,
        "automations": automations,
        "mcps": mcps,
    }


def sync_to_supabase(registry):
    """Sincroniza dados com Supabase via REST API."""
    import urllib.request

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }

    def upsert(table, data):
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            print(f"  ⚠ Supabase {table}: {e}", file=sys.stderr)

    # Upsert skills
    for skill in registry["skills"]:
        upsert("skills", [{
            "id": skill["id"], "name": skill["name"], "description": skill["description"],
            "type_label": skill["type_label"], "department": skill["department"],
            "icon": skill["icon"], "tags": skill["tags"], "status": skill["status"],
            "is_active": skill["status"] == "active", "path": skill["path"],
            "passos": skill["passos"], "tem_codigo": skill["tem_codigo"],
        }])

    # Upsert agents
    for dept_agents in registry["agents"].values():
        for agent in dept_agents:
            upsert("agents", [{
                "id": agent["id"], "name": agent["name"],
                "department": agent["department"], "level": agent["level"],
                "role": agent["role"], "framework": "Claude SDK",
                "status": agent["status"], "description": agent["description"],
                "icon": agent["icon"], "tags": agent["tags"],
            }])

    # Upsert automations
    for auto in registry.get("automations", []):
        upsert("automations", [{
            "id": auto["id"], "name": auto["name"],
            "type_label": auto.get("type_label"),
            "description": auto.get("description"),
            "trigger_type": auto.get("trigger_type"),
            "trigger_config": json.dumps(auto["trigger_config"]) if auto.get("trigger_config") else None,
            "agent_id": auto.get("agent_id"),
            "tags": auto.get("tags", []),
            "status": auto.get("status"),
            "icon": auto.get("icon"),
            "is_active": auto.get("status") == "active",
        }])

    # Upsert MCPs
    for mcp in registry.get("mcps", []):
        upsert("mcp_servers", [{
            "id": mcp["id"], "name": mcp["name"], "url": mcp["url"],
            "status": mcp["status"], "tools_available": mcp["tools_available"],
        }])

    # Log sync event
    upsert("activity_log", [{
        "action": f"sync-registry.py: {registry['stats']['habilidades_total']} skills, {registry['stats']['agentes_total']} agentes, {registry['stats']['mcps_connected']}/{registry['stats']['mcps_total']} MCPs",
        "event_type": "system",
        "details": json.dumps(registry["stats"]),
    }])


def main():
    registry = build_registry()

    # 1. Salvar registry.json (compatibilidade v1)
    OUTPUT.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

    stats = registry["stats"]
    print(f"✓ registry.json gerado em {OUTPUT}")
    print(f"  Habilidades: {stats['habilidades_total']} ({stats['habilidades_ativas']} ativas, {stats['habilidades_construcao']} em construção)")
    print(f"  Agentes: {stats['agentes_total']}")
    print(f"  Automações: {stats['automacoes_total']}")
    print(f"  MCPs: {stats['mcps_connected']}/{stats['mcps_total']} conectados")

    # 2. Sync com Supabase
    try:
        sync_to_supabase(registry)
        print("✓ Supabase sincronizado")
    except Exception as e:
        print(f"⚠ Supabase sync falhou: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
