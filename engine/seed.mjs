import {readFile,writeFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {envelope} from './project.mjs';

const snapshot=JSON.parse(gunzipSync(await readFile(new URL('../data/snapshot.json.gz',import.meta.url))));
const {rows}=envelope(snapshot);
await writeFile(process.argv[2], JSON.stringify({snapshot,rows,attempt:null})+'\n');
