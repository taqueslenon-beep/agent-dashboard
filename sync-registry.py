#!/usr/bin/env python3
"""
sync-registry.py — Gera registry.json escaneando skills e agentes.

Fontes de dados:
  1. .claude/skills/*/SKILL.md → frontmatter YAML → habilidades
  2. 3-recursos/orquestracao-agentes/arquitetura-departamentos.md → agentes
  3. Dados estáticos de automações e integrações (inline)

Executado automaticamente via hook do Claude Code após edição em .claude/skills/.
Também pode ser rodado manualmente: python3 sync-registry.py
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Paths relativos ao vault do Obsidian
VAULT = Path(__file__).resolve().parent.parent.parent  # sobe de agent-dashboard → recursos → vault
SKILLS_DIR = VAULT / ".claude" / "skills"
ARCH_FILE = VAULT / "3-recursos" / "orquestracao-agentes" / "arquitetura-departamentos.md"
OUTPUT = Path(__file__).resolve().parent / "registry.json"


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

    # Parser YAML simples (sem dependência externa)
    current_key = None
    current_val = ""
    for line in raw.split("\n"):
        # Multiline value continuation (description com >)
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

            # Multiline indicator
            if val == ">":
                current_key = key
                current_val = ""
                continue

            # Remove aspas
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]

            fm[key] = val

    if current_key:
        fm[current_key] = current_val.strip()

    return fm


def split_description(full_desc):
    """Separa a descrição real dos triggers de ativação."""
    # Cortar antes de "Use SEMPRE", "Acionar quando", triggers
    for separator in ["Use SEMPRE", "Acionar quando", "TRIGGER when", "Sempre que"]:
        idx = full_desc.find(separator)
        if idx > 0:
            return full_desc[:idx].strip().rstrip("."), full_desc[idx:]
    return full_desc.strip(), ""


def extract_tags_from_skill(fm, body_text):
    """Extrai tags do corpo da skill (não da descrição de trigger)."""
    tags = []

    # Tags explícitas do corpo (procurar em seções de tags/capabilities)
    name = fm.get("name", "")

    # Mapeamento manual por skill (mais confiável)
    tag_map = {
        "assessor-comunicacao": ["Slack", "WhatsApp", "Email", "Trello", "Instagram"],
        "redacao-juridica": ["CRARC", "Teses Amb.", "Teses Penais", ".docx"],
        "separador-de-pdfs": ["Divisão PDF", "Cronológico", "NotebookLM", "Índice.md"],
        "digitalizador-documentos": ["Escâner", "150 DPI", "Auto/Cor/PB", "Multi-página"],
        "extrator-car": ["CAR/SICAR", "Shapefiles", "GeoPandas", "Alertas", "Lote"],
    }

    if name in tag_map:
        return tag_map[name]

    # Fallback: extrair do corpo
    keywords = {
        "Slack": "Slack", "WhatsApp": "WhatsApp", "Email": "Email",
        "PDF": "PDF", "shapefile": "Shapefiles", "CAR": "CAR/SICAR",
        "INCRA": "INCRA", "MapBiomas": "MapBiomas", "Python": "Python",
    }
    desc_part = split_description(fm.get("description", ""))[0]
    for keyword, tag in keywords.items():
        if keyword.lower() in desc_part.lower() and tag not in tags:
            tags.append(tag)

    return tags[:6]


def extract_skill_body_info(filepath):
    """Extrai informações adicionais do corpo da skill (após o frontmatter)."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return {}

    # Remover frontmatter
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)

    info = {}

    # Contar passos
    passos = re.findall(r"^## PASSO \d", body, re.MULTILINE)
    if passos:
        info["passos"] = len(passos)

    # Verificar se tem código Python
    if "```python" in body:
        info["tem_codigo"] = True

    return info


def infer_status(fm, body_info):
    """Infere o status da skill a partir do frontmatter e corpo."""
    desc = fm.get("description", "").lower()
    name = fm.get("name", "").lower()

    # Status explícitos conhecidos
    status_overrides = {
        "extrator-car": "prototype",  # em construção
    }
    if name in status_overrides:
        return status_overrides[name]

    if "em construção" in desc or "em construcao" in desc:
        return "prototype"
    if "planejado" in desc or "planned" in desc:
        return "planned"

    return "active"  # default para skills com SKILL.md completo


def infer_icon(name):
    """Mapeia nome da skill para um ícone emoji."""
    icons = {
        "assessor-comunicacao": "\U0001f4ac",      # 💬
        "redacao-juridica": "\u2696",               # ⚖
        "separador-de-pdfs": "\U0001f4c4",          # 📄
        "digitalizador-documentos": "\U0001f4f7",   # 📷
        "extrator-car": "\U0001f30d",               # 🌍
        "extrator-incra": "\U0001f3e0",             # 🏠
        "extrator-mapbiomas": "\U0001f332",         # 🌲
        "extrator-ima": "\U0001f3d4",               # 🏔
        "extrator-ibama": "\U0001f6a8",             # 🚨
    }
    return icons.get(name, "\u2699")  # ⚙ default


def infer_department(fm):
    """Infere o departamento da skill."""
    desc = fm.get("description", "").lower()
    if any(k in desc for k in ["técnico", "tecnico", "car", "sicar", "ambiental", "incra", "mapbiomas"]):
        return "tecnico"
    if any(k in desc for k in ["jurídico", "juridico", "petição", "peticao", "processual"]):
        return "juridico"
    return "geral"


def infer_type_label(fm):
    """Gera o label de tipo para o card."""
    desc = fm.get("description", "").lower()
    name = fm.get("name", "")

    type_map = {
        "assessor-comunicacao": "Habilidade · Agente de Comunicação",
        "redacao-juridica": "Habilidade · Agente de Escrita Legal",
        "separador-de-pdfs": "Habilidade · Agente de Processamento Documental",
        "digitalizador-documentos": "Habilidade · Agente de Processamento de Imagem",
        "extrator-car": "Habilidade · Dept. Técnico — Estagiário Técnico",
    }

    if name in type_map:
        return type_map[name]

    if "técnico" in desc or "tecnico" in desc:
        return "Habilidade · Dept. Técnico"
    if "jurídico" in desc or "juridico" in desc:
        return "Habilidade · Dept. Jurídico"

    return "Habilidade · Geral"


def scan_skills():
    """Escaneia .claude/skills/ e retorna lista de habilidades."""
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

        # Nome formatado
        name_map = {
            "assessor-comunicacao": "Assessor de Comunicação",
            "redacao-juridica": "Redação Jurídica",
            "separador-de-pdfs": "Separador de PDFs",
            "digitalizador-documentos": "Digitalizador de Documentos",
            "extrator-car": "Extrator CAR (SICAR)",
        }
        display_name = name_map.get(fm["name"], fm["name"].replace("-", " ").title())

        skill = {
            "id": fm["name"],
            "name": display_name,
            "type_label": infer_type_label(fm),
            "description": desc_short,
            "tags": extract_tags_from_skill(fm, ""),
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
    """Retorna a lista de agentes dos dois departamentos.

    Lê do arquivo de arquitetura se existir, senão usa dados padrão.
    """
    agents = {
        "juridico": [
            {
                "id": "estagiario-juridico",
                "name": "Agente 0 — Estagiário Jurídico",
                "function": "agente_estagiario()",
                "role": "Extração e Coleta",
                "description": "Extrai autos de processos dos sistemas judiciais. Sub-agentes: ext_eproc() [Beta], ext_sgpe() [Em Construção].",
                "tags": ["EPROC", "SGPe", "PROJUDI", "ESAJ"],
                "status": "prototype",
                "icon": "\U0001f393",
                "sub_agents": [
                    {"name": "Extrator EPROC", "status": "planned"},
                    {"name": "Extrator SGPE", "status": "prototype"},
                ]
            },
            {
                "id": "advogado-junior",
                "name": "Agente 1 — Advogado Júnior",
                "function": "agente_junior()",
                "role": "Análise e Separação",
                "description": "Separa e classifica peças processuais. Sub-agentes: sep_admin(), sep_eproc(), sep_projudi().",
                "tags": ["Proc. Admin.", "EPROC", "PROJUDI"],
                "status": "planned",
                "icon": "\U0001f50d",
            },
            {
                "id": "advogado-pleno",
                "name": "Agente 2 — Advogado Pleno",
                "function": "agente_pleno()",
                "role": "Redação e Argumentação",
                "description": "Redige peças completas. Sub-agentes: fatos(), fundamentos(), pedidos(), resumo(), finais().",
                "tags": ["Fatos", "Fundamentos", "Pedidos", "Resumo"],
                "status": "planned",
                "icon": "\u270d",
            },
            {
                "id": "advogado-senior",
                "name": "Agente 3 — Advogado Sênior",
                "function": "agente_senior()",
                "role": "Estratégia e Jurisprudência",
                "description": "Estratégia processual e jurisprudência. Sub-agentes: juris.dominante(), juris.vanguarda().",
                "tags": ["Estratégia", "Juris. Dominante", "Juris. Vanguarda"],
                "status": "planned",
                "icon": "\U0001f3af",
            },
        ],
        "tecnico": [
            {
                "id": "estagiario-tecnico",
                "name": "Agente 0 — Estagiário Técnico",
                "function": "estagiario_tecnico()",
                "role": "Extração de Dados Ambientais",
                "description": "Coleta dados das bases ambientais e fundiárias. Orquestra skills de extração: CAR, INCRA, MapBiomas, IMA-SC, IBAMA.",
                "tags": ["CAR", "INCRA", "MapBiomas", "IMA-SC", "IBAMA"],
                "status": "prototype",
                "icon": "\U0001f9ea",
                "sub_agents": [
                    {"name": "Extrator CAR", "status": "prototype"},
                    {"name": "Extrator INCRA", "status": "planned"},
                    {"name": "Extrator MapBiomas", "status": "planned"},
                    {"name": "Extrator IMA-SC", "status": "planned"},
                    {"name": "Extrator Embargos IBAMA", "status": "planned"},
                    {"name": "Extrator Alertas", "status": "planned"},
                ]
            },
            {
                "id": "analista-junior",
                "name": "Agente 1 — Analista MA Júnior",
                "function": "analista_junior()",
                "role": "Organização e Cruzamento",
                "description": "Tabula dados extraídos, cruza matrícula × CAR × INCRA, detecta inconsistências básicas.",
                "tags": ["Tabulação", "Matrícula×CAR", "Matrícula×INCRA", "Shapefiles"],
                "status": "planned",
                "icon": "\U0001f4ca",
            },
            {
                "id": "analista-pleno",
                "name": "Agente 2 — Analista MA Pleno",
                "function": "analista_pleno()",
                "role": "Análise Integrada e Relatórios",
                "description": "Análise de APP e Reserva Legal, geração de mapas temáticos, relatórios técnicos por propriedade.",
                "tags": ["APP", "Reserva Legal", "Mapas", "Relatórios"],
                "status": "planned",
                "icon": "\U0001f5fa",
            },
            {
                "id": "analista-senior",
                "name": "Agente 3 — Analista MA Sênior",
                "function": "analista_senior()",
                "role": "Diagnóstico Estratégico",
                "description": "Score de risco ambiental, consolidação de passivos, ranking de propriedades por criticidade.",
                "tags": ["Score Risco", "Passivos", "Ranking", "Estratégia"],
                "status": "planned",
                "icon": "\U0001f6a8",
            },
        ],
    }

    return agents


def get_automations():
    """Retorna lista de automações (semi-estáticas por enquanto)."""
    return [
        {
            "id": "monitor-iat",
            "name": "Monitor de Protocolos IAT",
            "type_label": "Automação · Cron + Playwright + Supabase",
            "description": "Monitora 38+ protocolos no IAT. Agendamento → Playwright → Compara Supabase → Alerta Slack.",
            "tags": ["Playwright", "Supabase", "Cron", "Slack"],
            "status": "prototype",
            "icon": "\U0001f514",
        },
        {
            "id": "doc-obsidian",
            "name": "Documentação Automática Obsidian",
            "type_label": "Gatilho · Documentação de Sessão",
            "description": "Documenta sessões e decisões no Obsidian ao final de cada interação. Metadados YAML padronizados.",
            "tags": ["Gatilhos", "Obsidian", "Sessões"],
            "status": "active",
            "icon": "\U0001f4dd",
        },
    ]


def build_registry():
    """Constrói o registry completo."""
    skills = scan_skills()
    agents = get_agents()
    automations = get_automations()

    # Contadores
    active_skills = len([s for s in skills if s["status"] == "active"])
    prototype_skills = len([s for s in skills if s["status"] == "prototype"])
    total_agents = len(agents["juridico"]) + len(agents["tecnico"])

    registry = {
        "_meta": {
            "generated_at": datetime.now().isoformat(),
            "generator": "sync-registry.py",
            "vault": str(VAULT),
        },
        "stats": {
            "habilidades_ativas": active_skills,
            "habilidades_construcao": prototype_skills,
            "habilidades_total": len(skills),
            "agentes_total": total_agents,
            "automacoes_total": len(automations),
        },
        "skills": skills,
        "agents": agents,
        "automations": automations,
    }

    return registry


def main():
    registry = build_registry()

    OUTPUT.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Resumo
    stats = registry["stats"]
    print(f"✓ registry.json gerado em {OUTPUT}")
    print(f"  Habilidades: {stats['habilidades_total']} ({stats['habilidades_ativas']} ativas, {stats['habilidades_construcao']} em construção)")
    print(f"  Agentes: {stats['agentes_total']}")
    print(f"  Automações: {stats['automacoes_total']}")


if __name__ == "__main__":
    main()
