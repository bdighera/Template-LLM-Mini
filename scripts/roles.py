"""Role definitions in agents/<role>.md: frontmatter policy plus a short role prompt.

Roles with `requires: <addon>` load automatically when that add-on is enabled and
are ignored otherwise; all other roles must be listed in config.roles.
"""
from pathlib import Path

REQUIRED_ROLES = ('builder', 'reviewer', 'tester')
KEYS = {'name', 'description', 'runtime', 'model_hint', 'writes', 'tools', 'independent_of', 'requires'}
ADDONS = ('data_analysis',)
RUNTIMES = {'any', 'claude', 'codex'}
MODEL_HINTS = {'inherit': 'inherit', 'fast': 'haiku', 'balanced': 'sonnet', 'strong': 'opus'}
TOOLS = {'read': ['Read', 'Grep', 'Glob'], 'edit': ['Edit', 'Write'], 'shell': ['Bash'], 'web': ['WebFetch', 'WebSearch']}
GENERATED_NOTE = '<!-- Generated from {source} by `workflow.py sync-agents`; edit the source instead. -->'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def parse(path):
    content = Path(path).read_text(encoding='utf-8')
    require(content.startswith('---\n') and '\n---\n' in content[4:], f'{path}: missing frontmatter')
    header, body = content[4:].split('\n---\n', 1)
    meta = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(':')
        require(separator, f'{path}: invalid frontmatter line {line!r}')
        value = value.strip()
        if value.startswith('[') and value.endswith(']'):
            value = [item.strip() for item in value[1:-1].split(',') if item.strip()]
        meta[key.strip()] = value
    require(set(meta) == KEYS, f'{path}: frontmatter keys must be {sorted(KEYS)}')
    require(meta['name'] == Path(path).stem, f'{path}: name must match the file name')
    require(meta['runtime'] in RUNTIMES, f'{path}: runtime must be one of {sorted(RUNTIMES)}')
    require(meta['model_hint'] in MODEL_HINTS, f'{path}: model_hint must be one of {sorted(MODEL_HINTS)}')
    require(isinstance(meta['writes'], list) and isinstance(meta['tools'], list), f'{path}: writes/tools must be [lists]')
    require(set(meta['tools']) <= set(TOOLS), f'{path}: tools must be among {sorted(TOOLS)}')
    require(meta['independent_of'] == 'none' or isinstance(meta['independent_of'], str), f'{path}: independent_of is a role or none')
    require(meta['requires'] in ('none', *ADDONS), f'{path}: requires must be none or one of {list(ADDONS)}')
    meta['body'] = body.strip()
    meta['source'] = Path(path)
    return meta


def configured(root, config):
    """Listed roles plus every add-on role whose add-on is enabled."""
    result = {}
    for name in config['roles']:
        path = root / 'agents' / f'{name}.md'
        require(path.is_file(), f'Role {name} needs a definition file in agents/{name}.md')
        result[name] = parse(path)
        require(result[name]['requires'] == 'none',
                f'Role {name} comes from the {result[name]["requires"]} add-on; enable the add-on instead of listing it')
    for path in sorted((root / 'agents').glob('*.md')):
        if path.stem not in result:
            meta = parse(path)
            if meta['requires'] != 'none' and config['addons'].get(meta['requires']):
                result[path.stem] = meta
    for meta in result.values():
        require(meta['independent_of'] in ('none', *result), f'{meta["name"]}: unknown independent_of role')
    return result


def policy(meta):
    return {key: meta[key] for key in ('runtime', 'model_hint', 'writes', 'tools', 'independent_of', 'requires')}


def sync(root, definitions):
    """Write .claude/agents/<role>.md subagents; Codex uses agents/<role>.md directly."""
    target = root / '.claude/agents'
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, meta in definitions.items():
        path = target / f'{name}.md'
        if meta['runtime'] == 'codex':
            if path.exists():
                path.unlink()
            continue
        tools = [tool for key in meta['tools'] for tool in TOOLS[key]]
        source = meta['source'].relative_to(root).as_posix()
        content = '\n'.join(['---', f'name: {name}', f'description: {meta["description"]}',
                             f'tools: {", ".join(tools)}', f'model: {MODEL_HINTS[meta["model_hint"]]}', '---',
                             GENERATED_NOTE.format(source=source), '',
                             'Shared rules: AGENTS.md. Return results to the coordinator; never edit workflow state.', '',
                             meta['body'], ''])
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
        written.append(path.relative_to(root).as_posix())
    return written
