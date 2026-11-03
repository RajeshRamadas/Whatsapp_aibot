import { readFileSync, writeFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { gzipSync } from 'node:zlib';

const source = readFileSync(new URL('./widget.ts', import.meta.url), 'utf8');
const output = stripTypeScriptTypes(source, { mode: 'strip' });
const path = new URL('./widget.js', import.meta.url);
const bytes = gzipSync(output).length;
if (bytes >= 30 * 1024) throw new Error('Widget exceeds the 30 KiB gzip budget');
if (process.argv.includes('--check')) {
  if (readFileSync(path, 'utf8') !== output) throw new Error('Run node widget/build.mjs to rebuild widget.js');
} else {
  writeFileSync(path, output);
}
console.log('Widget gzip size:', bytes, 'bytes');
