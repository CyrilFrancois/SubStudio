// gen-types.mjs — Generates src/lib/api.gen.ts from the backend's OpenAPI (backend/api). Exports: none (script).
// `node scripts/gen-types.mjs`          rewrites the file.
// `node scripts/gen-types.mjs --check`  exits 1 when the committed file differs from the backend: the
//                                        mechanism that keeps types.ts honest, instead of a comment.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString } from 'openapi-typescript';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..', '..');
const out = join(here, '..', 'src', 'lib', 'api.gen.ts');

const venvPy = join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.SUBSTUDIO_PYTHON || (existsSync(venvPy) ? venvPy : 'python3');

const spec = JSON.parse(execFileSync(python, [join(repo, 'backend', 'api', 'export_openapi.py')], { encoding: 'utf8' }));
// defaultNonNullable: false — a field with a default is optional in a request body (JobCreate.options).
const header = '// api.gen.ts — GENERATED from backend/api by scripts/gen-types.mjs; do not edit. Exports: paths, components.\n';
const body = header + astToString(await openapiTS(spec, { alphabetize: true, defaultNonNullable: false }));

if (process.argv.includes('--check')) {
  const current = existsSync(out) ? readFileSync(out, 'utf8').replace(/\r\n/g, '\n') : '';
  if (current !== body) {
    console.error('src/lib/api.gen.ts is stale: run `npm run gen:types` and commit it.');
    process.exit(1);
  }
  console.log('api.gen.ts matches the backend contract.');
} else {
  writeFileSync(out, body);
  console.log(`wrote ${out}`);
}
