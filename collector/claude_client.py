"""Anthropic Claude core brain for CrowdStage conference enrichment.
High-speed, high-accuracy structured evidence selection using Claude 4.5.
"""
from __future__ import annotations
import json
import logging
import os
import requests

LOG = logging.getLogger('crowdstage-claude')
API_URL = 'https://api.anthropic.com/v1/messages'
MODELS_URL = 'https://api.anthropic.com/v1/models'
ANTHROPIC_VERSION = '2023-06-01'
DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
SONNET_MODEL = 'claude-sonnet-4-5-20250929'

SELECTION_SCHEMA = {
    'type': 'object',
    'properties': {
        'selections': {
            'type': 'array',
            'maxItems': 8,
            'items': {
                'type': 'object',
                'properties': {
                    'field': {'type': 'string'},
                    'node_id': {'type': 'integer'}
                },
                'required': ['field', 'node_id'],
                'additionalProperties': False
            }
        }
    },
    'required': ['selections'],
    'additionalProperties': False
}

RESEARCH_SCHEMA = {
    'type': 'object',
    'properties': {
        'evidence': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'field': {'type': 'string'},
                    'value': {'type': 'string'},
                    'quote': {'type': 'string'},
                    'page_url': {'type': 'string'},
                    'event_title': {'type': 'string'}
                },
                'required': ['field', 'value', 'quote', 'page_url', 'event_title'],
                'additionalProperties': False
            }
        }
    },
    'required': ['evidence'],
    'additionalProperties': False
}

def configured() -> bool:
    return (os.environ.get('CLAUDE_ENRICHMENT_ENABLED', '1') == '1' and
            bool(os.environ.get('ANTHROPIC_API_KEY')))

def get_model(prefer_sonnet: bool = False) -> str:
    if prefer_sonnet:
        return os.environ.get('ANTHROPIC_SONNET_MODEL') or SONNET_MODEL
    return os.environ.get('ANTHROPIC_MODEL') or DEFAULT_MODEL


class ClaudeClient:
    def __init__(self, key: str | None = None, model: str | None = None):
        self.key = key or os.environ.get('ANTHROPIC_API_KEY')
        self.model = model or get_model()
        self.session = requests.Session()
        self.session.trust_env = False

    def close(self):
        self.session.close()

    def _headers(self) -> dict:
        if not self.key:
            raise RuntimeError('missing_key')
        return {
            'x-api-key': self.key,
            'anthropic-version': ANTHROPIC_VERSION,
            'content-type': 'application/json'
        }

    def authenticate(self) -> dict:
        """Verify API key and return provider status dictionary."""
        if not self.key:
            return {
                'provider': 'claude',
                'configured': False,
                'credential_available': False,
                'authenticated': False,
                'reason': 'missing_key',
                'model': self.model
            }
        try:
            resp = self.session.get(MODELS_URL, headers={'x-api-key': self.key, 'anthropic-version': ANTHROPIC_VERSION}, timeout=10)
            if resp.status_code == 200:
                return {
                    'provider': 'claude',
                    'configured': True,
                    'credential_available': True,
                    'authenticated': True,
                    'task_creation_allowed': True,
                    'reason': 'active_core_brain',
                    'model': self.model
                }
            return {
                'provider': 'claude',
                'configured': True,
                'credential_available': True,
                'authenticated': False,
                'task_creation_allowed': False,
                'reason': f'http_{resp.status_code}',
                'model': self.model
            }
        except Exception as exc:
            return {
                'provider': 'claude',
                'configured': True,
                'credential_available': True,
                'authenticated': False,
                'task_creation_allowed': False,
                'reason': str(exc)[:200],
                'model': self.model
            }

    def select_evidence(self, payload: dict, instructions: str, timeout: int = 25) -> tuple[dict, dict]:
        """Extract structured node selections matching SELECTION_SCHEMA."""
        tools = [{
            'name': 'evidence_selection',
            'description': 'Select directly observed evidence nodes for ONLY the requested missing fields.',
            'input_schema': SELECTION_SCHEMA
        }]
        body = {
            'model': self.model,
            'max_tokens': 450,
            'system': instructions,
            'messages': [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=True)}],
            'tools': tools,
            'tool_choice': {'type': 'tool', 'name': 'evidence_selection'}
        }
        resp = self.session.post(API_URL, headers=self._headers(), json=body, timeout=timeout, allow_redirects=False)
        if resp.status_code != 200:
            self._handle_error(resp)
        data = resp.json()
        usage = data.get('usage', {})
        for block in data.get('content', []):
            if block.get('type') == 'tool_use' and block.get('name') == 'evidence_selection':
                return block.get('input', {}), usage
        for block in data.get('content', []):
            if block.get('type') == 'text':
                try: return json.loads(block.get('text', '')), usage
                except Exception: pass
        raise RuntimeError('invalid_response')

    def research_fields(self, event_title: str, url: str, fields: list[str], page_text: str, timeout: int = 35) -> tuple[list[dict], dict]:
        """Extract missing fields with verbatim quotes from page text."""
        system_prompt = (
            'You are CrowdStage Core Intelligence. Research and extract ONLY directly observed conference fields '
            'from the provided organizer page text. Never guess, invent, or extrapolate. Provide exact direct quotes '
            'from the text verifying each field value. Match the specified conference edition strictly.'
        )
        payload = {
            'event': event_title,
            'url': url,
            'fields_requested': fields,
            'page_excerpt': page_text[:12000]
        }
        tools = [{
            'name': 'research_evidence',
            'description': 'Extract missing conference fields with exact verbatim quotes.',
            'input_schema': RESEARCH_SCHEMA
        }]
        body = {
            'model': self.model,
            'max_tokens': 800,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=True)}],
            'tools': tools,
            'tool_choice': {'type': 'tool', 'name': 'research_evidence'}
        }
        resp = self.session.post(API_URL, headers=self._headers(), json=body, timeout=timeout, allow_redirects=False)
        if resp.status_code != 200:
            self._handle_error(resp)
        data = resp.json()
        usage = data.get('usage', {})
        for block in data.get('content', []):
            if block.get('type') == 'tool_use' and block.get('name') == 'research_evidence':
                inp = block.get('input', {})
                return inp.get('evidence', []), usage
        return [], usage

    def _handle_error(self, resp):
        try: err = resp.json().get('error', {})
        except Exception: err = {}
        msg = err.get('message', '')
        err_type = err.get('type', '')
        if resp.status_code == 429 or 'rate_limit' in err_type:
            raise RuntimeError('provider_rate_limit_exceeded')
        if 'credit' in msg.lower() or 'quota' in msg.lower() or 'insufficient' in err_type:
            raise RuntimeError('provider_insufficient_quota')
        raise RuntimeError(f'provider_http_{resp.status_code}')
