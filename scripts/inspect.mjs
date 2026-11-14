// Read the live contracts on GenLayer Bradbury testnet.
//
//   npm install
//   node scripts/inspect.mjs                # list every oracle check and task
//   node scripts/inspect.mjs <check_id>     # one check, plus a local hash re-check
//
// No wallet is needed: these are view calls.

import { createHash } from 'node:crypto';
import { createClient } from 'genlayer-js';
import { testnetBradbury } from 'genlayer-js/chains';

const ORACLE = '0xAc46eBFC51A34f171AB9E0146a62F4f0df8a541A';
const BOARD = '0xB7C85027b8D16D3e50413E8F3761D477c7375168';
// The public chain RPC does not serve gen_call; the GenLayer node RPC does.
const client = createClient({ chain: testnetBradbury, endpoint: 'https://rpc-bradbury.genlayer.com' });

const plain = (value) => (value instanceof Map ? Object.fromEntries(value) : value);
const read = async (address, functionName, args = []) =>
  plain(await client.readContract({ address, functionName, args }));

async function recheckHash(check) {
  const url = `https://raw.githubusercontent.com/${check.owner}/${check.repo}/${check.commit}/${check.path}`;
  const body = Buffer.from(await (await fetch(url)).arrayBuffer());
  const local = createHash('sha256').update(body).digest('hex');
  return { url, local, matches: local === check.content_hash };
}

const wanted = process.argv[2];
if (wanted) {
  const check = await read(ORACLE, 'get_check', [wanted]);
  console.log(check);
  if (check.http_status === '200') console.log(await recheckHash(check));
} else {
  const count = Number(await read(ORACLE, 'get_check_count'));
  console.log(`EvidenceOracle ${ORACLE}: ${count} checks`);
  for (let i = 0; i < count; i++) {
    const id = await read(ORACLE, 'get_check_id_at', [i]);
    const c = await read(ORACLE, 'get_check', [id]);
    console.log(`  ${id}  ${c.verdict.padEnd(12)} ${c.owner}/${c.repo}@${c.commit.slice(0, 7)}:${c.path}  "${c.claim}"`);
  }
  const tasks = Number(await read(BOARD, 'get_task_count'));
  console.log(`BountyBoard ${BOARD}: ${tasks} tasks`);
  for (let i = 1; i <= tasks; i++) {
    const t = await read(BOARD, 'get_task', [`T${i}`]);
    console.log(`  ${t.task_id}  ${t.status.padEnd(6)} ${t.reward_points} pts  solver=${t.solver || '-'}  "${t.requirement}"`);
  }
}
