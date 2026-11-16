"""Actual file delivery for the isolated hardening acceptance, keyed by commit."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from verify_live_repair import require_inside, validate_root


def deliver(root, destination):
    root = validate_root(root)
    source = require_inside(root, Path.cwd())
    destination = require_inside(root, destination)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    target = destination / head
    paths = subprocess.check_output(['git', 'ls-files', '-z']).decode('utf-8').split('\0')
    hashes = {}
    target.mkdir(parents=True, exist_ok=True)
    for relative in filter(None, paths):
        original = require_inside(source, source / relative)
        output = require_inside(target, target / relative)
        content = original.read_bytes()
        hashes[relative] = hashlib.sha256(content).hexdigest()
        if not output.is_file() or output.read_bytes() != content:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name + '.pending')
            shutil.copyfile(original, temporary)
            temporary.replace(output)
    receipt = {'commit': head, 'files': hashes}
    temporary = destination / 'receipt.pending'
    temporary.write_text(json.dumps(receipt, sort_keys=True), encoding='utf-8')
    temporary.replace(destination / 'receipt.json')
    print('Delivered and verified commit ' + head, flush=True)
    return receipt


if __name__ == '__main__':
    deliver(Path(sys.argv[1]), Path(sys.argv[2]))
