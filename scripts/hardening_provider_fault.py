"""Acceptance-only CLI boundary: one empty repair, one malformed diagnosis.

The real runner and scheduler consume these outcomes. Never writes HQ database
states or acceptance results. All other calls go to the actual Codex CLI.
"""
import json
from pathlib import Path
import re
import subprocess
import sys


def claim(folder, name):
    try:
        with (folder / name).open('x', encoding='utf-8') as stream:
            stream.write('injected at provider boundary\n')
        return True
    except FileExistsError:
        return False


def prompt_file(arguments):
    # report_prompt intentionally removes quotes before passing through Windows CLI.
    match = re.search(r'Read the file (.+?) completely and carry out', arguments[-1]) if arguments else None
    return Path(match[1].strip('"')) if match else None


def main():
    folder, cli, *args = sys.argv[1:]
    folder = Path(folder)
    prompt_path = prompt_file(args)
    prompt = prompt_path.read_text(encoding='utf-8') if prompt_path else ''
    if 'REQUIRED TARGETED VERIFICATION' in prompt and claim(folder, 'empty-repair'):
        print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
              'text': 'Acceptance fault injection: this repair ended without changing files. '
                      'The original integration failure remains. No successful repair is claimed.'}}), flush=True)
        print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 0, 'output_tokens': 0}}), flush=True)
        return 0
    result = subprocess.run([cli, *args])
    report = prompt_path.parent / 'repair-diagnosis.json' if prompt_path else None
    if result.returncode == 0 and report and report.is_file() and 'REQUIRED TARGETED VERIFICATION' not in prompt:
        if claim(folder, 'malformed-diagnosis'):
            original = report.read_bytes()
            report.with_name('original-provider-diagnosis.json').write_bytes(original)
            record = json.loads(original)
            record.pop('why_new', None)
            report.write_text(json.dumps(record), encoding='utf-8')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
