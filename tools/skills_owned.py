"""Profile-owned skill documents without activation scripts or credential capture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

MAX_BYTES = 512 * 1024


def _read(root, parts):
    if not parts or any(p in {'', '.', '..'} or '/' in p or '\\' in p for p in parts):
        raise ValueError('Skill path must stay inside the profile skill directory')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ValueError('Skill document is not a bounded regular file')
            data = source.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError('Skill document exceeds its limit')
            return data.decode('utf-8')
    finally:
        os.close(fd)


def dispatch(name, args):
    from tools.capability_broker import require_live_policy, tool_action, issue_capability, consume_capability
    from agent.budget_account import budget_tool_scope
    from agent.runtime_commands import assert_runtime_dispatch
    context = require_live_policy()
    action = tool_action(name, args)
    consume_capability(issue_capability(action), action)
    root = Path(context.profile_home) / 'skills'
    with budget_tool_scope(assert_runtime_dispatch(), name, at_execution=True):
        if name == 'skills_list':
            items = []
            # Profile-local installed documents only. Never load plugin factories.
            for base, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not Path(base, d).is_symlink())
                if 'SKILL.md' not in files:
                    continue
                relative = Path(base).relative_to(root)
                category = args.get('category')
                if category and (not relative.parts or relative.parts[0] != category):
                    continue
                try:
                    content = _read(root, (*relative.parts, 'SKILL.md'))
                except (OSError, ValueError, UnicodeError):
                    continue
                from tools.skills_tool import _safe_frontmatter
                meta = _safe_frontmatter(content=content)
                items.append({'name': str(relative), 'description': str(meta.get('description', ''))[:1024]})
                if len(items) >= 1024:
                    break
            return json.dumps({'skills': items})
        skill = args.get('name')
        support = args.get('file_path') or 'SKILL.md'
        if not isinstance(skill, str) or not isinstance(support, str):
            raise ValueError('Skill and support paths must be strings')
        if support != 'SKILL.md' and support.split('/')[0] not in {'references', 'scripts', 'templates', 'assets'}:
            raise PermissionError('Only skill instructions and declared support directories are readable; secrets are excluded')
        content = _read(root, (*skill.split('/'), *support.split('/')))
        return json.dumps({'name': skill, 'file_path': support, 'content': content,
                           'activation': 'document_only', 'scripts': 'Use separately granted execution tools'})
