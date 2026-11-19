// gen-types.mjs — Generates src/lib/openapi.json (contract snapshot) and src/lib/api.gen.ts from backend/api. Exports: none (script).
// `node scripts/gen-types.mjs`          rewrites both files.
// `node scripts/gen-types.mjs --check`  exits 1 when either committed file differs from the backend.
// The snapshot is also compared by backend/tests/test_contract.py, so every pytest run catches a
// backend change that was not regenerated: the mechanism that keeps types.ts honest, not a comment.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..', '..');
const lib = join(here, '..', 'src', 'lib');
const outTs = join(lib, 'api.gen.ts');
const outJson = join(lib, 'openapi.json');

const venvPy = join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.SUBSTUDIO_PYTHON || (existsSync(venvPy) ? venvPy : 'python3');

const raw = execFileSync(python, [join(repo, 'backend', 'api', 'export_openapi.py')], { encoding: 'utf8' });
const spec = JSON.parse(raw);
const json = JSON.stringify(spec, null, 2) + '\n';
// defaultNonNullable: false — a field with a default is optional in a request body (JobCreate.options).
const header = '// api.gen.ts — GENERATED from src/lib/openapi.json by scripts/gen-types.mjs; do not edit. Exports: paths, components.\n';
const ts = header + astToString(await openapiTS(spec, { alphabetize: true, defaultNonNullable: false }));

const read = (p) => (existsSync(p) ? readFileSync(p, 'utf8').replace(/\r\n/g, '\n') : '');

if (process.argv.includes('--check')) {
  const stale = [[outJson, json], [outTs, ts]].filter(([p, want]) => read(p) !== want).map(([p]) => p);
  if (stale.length) {
    console.error(`stale: ${stale.join(', ')} — run \`npm run gen:types\` and commit both files.`);
    process.exit(1);
  }
  console.log('openapi.json and api.gen.ts match the backend contract.');
} else {
  writeFileSync(outJson, json);
  writeFileSync(outTs, ts);
  console.log(`wrote ${outJson} and ${outTs}`);
}
