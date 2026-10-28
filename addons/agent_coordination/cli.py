#!/usr/bin/env python3
"""Optional local coordination service, explicit operator approvals, and MCP adapter."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('serve', 'approve'):
        p = commands.add_parser(command)
        p.add_argument('--root', required=True, help='Configured authoritative workflow checkout')
        p.add_argument('--settings', required=True, help='Local identity/permission settings JSON')
        if command == 'serve':
            p.add_argument('--port', type=int, default=8765)
        else:
            p.add_argument('--file', required=True, help='Exact assignment JSON approved by the user')
            p.add_argument('--approved', action='store_true')
            p.add_argument('--ttl', type=int, default=3600)
    p = commands.add_parser('mcp')
    p.add_argument('--url', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    if args.command == 'mcp':
        from client import Client
        from mcp_adapter import create_mcp
        create_mcp(Client(args.url, os.environ.get('AGENT_COORD_TOKEN', ''))).run(transport='stdio')
        return
    from contracts import Assignment
    from service import Service
    settings = json.loads(Path(args.settings).read_text(encoding='utf-8'))
    service = Service(args.root, settings, load_credentials=args.command == 'serve')
    if args.command == 'approve':
        if not args.approved:
            parser.error('Explicit user approval requires --approved; agents must not approve for the user')
        assignment = Assignment.model_validate_json(Path(args.file).read_text(encoding='utf-8'))
        print(json.dumps(service.approval(assignment, args.ttl), indent=2))
    else:
        if not 1 <= args.port <= 65535:
            parser.error('Port must be 1..65535')
        import uvicorn
        from api import create_app
        uvicorn.run(create_app(service), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # No request payloads, raw credentials, or environment dumps on failure.
        print(f'Coordination error: {type(exc).__name__}: check settings and workflow state.', file=sys.stderr)
        sys.exit(2)
