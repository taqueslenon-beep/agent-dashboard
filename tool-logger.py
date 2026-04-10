#!/usr/bin/env python3
"""
Tool Logger — Observabilidade de MCPs e Skills
Disparado via hook PostToolUse do Claude Code.
Lê JSON do stdin, atualiza tool-status.json na pasta do dashboard.
"""
import json, sys, os
from datetime import datetime, timezone

# Nomes amigáveis para cada MCP
MCP_LABELS = {
    'trello':                   'Trello (base)',
    'trello-extended':          'Trello Extended',
    'todoist':                  'Todoist',
    'whatsapp':                 'WhatsApp',
    'notebooklm-mcp':           'NotebookLM',
    'playwright':               'Playwright',
    'claude_ai_Google_Calendar':'Google Calendar',
    'claude_ai_Gmail':          'Gmail',
    'claude_ai_Slack':          'Slack',
    'claude_ai_Canva':          'Canva',
    'claude_ai_Supabase':       'Supabase',
    'bnp-api':                  'BNP API',
}

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_PATH   = os.path.join(DASHBOARD_DIR, 'tool-status.json')

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def extract_error(tool_response):
    if not isinstance(tool_response, dict):
        return False, None
    if not tool_response.get('is_error'):
        return False, None
    content = tool_response.get('content', '')
    if isinstance(content, list):
        msg = ' '.join(
            c.get('text', '') for c in content if isinstance(c, dict)
        )
    else:
        msg = str(content)
    return True, msg[:300]

def categorize(tool_name, tool_input):
    if tool_name.startswith('mcp__'):
        parts = tool_name.split('__')
        server = parts[1] if len(parts) > 1 else tool_name
        action = '__'.join(parts[2:]) if len(parts) > 2 else ''
        label  = MCP_LABELS.get(server, server)
        return 'mcp', server, label, action
    if tool_name == 'Skill':
        skill_id = tool_input.get('skill', 'unknown')
        return 'skill', skill_id, skill_id, ''
    return 'builtin', tool_name, tool_name, ''

def load_status():
    try:
        return json.load(open(STATUS_PATH))
    except Exception:
        return {'mcps': {}, 'skills': {}, 'updated': None}

def save_status(status):
    with open(STATUS_PATH, 'w') as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name     = data.get('tool_name', 'unknown')
    tool_input    = data.get('tool_input', {})
    tool_response = data.get('tool_response', {})

    is_error, error_msg = extract_error(tool_response)
    category, key, label, action = categorize(tool_name, tool_input)

    if category == 'builtin':
        sys.exit(0)  # não rastrear ferramentas internas do Claude Code

    status = load_status()
    bucket = 'mcps' if category == 'mcp' else 'skills'

    if key not in status[bucket]:
        status[bucket][key] = {
            'label':       label,
            'calls':       0,
            'errors':      0,
            'last_call':   None,
            'last_status': None,
            'last_error':  None,
            'last_action': None,
        }

    entry = status[bucket][key]
    entry['label']       = label
    entry['calls']      += 1
    entry['last_call']   = now_iso()
    entry['last_action'] = action or tool_name
    entry['last_status'] = 'error' if is_error else 'ok'

    if is_error:
        entry['errors']     += 1
        entry['last_error']  = error_msg

    status['updated'] = now_iso()
    save_status(status)

if __name__ == '__main__':
    main()
