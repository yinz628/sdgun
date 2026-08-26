// 校验类型双副本一致:根目录 sdgun-api-types.ts(主文件)与
// webui/src/types/sdgun-api-types.ts(构建用副本)。
// 双副本靠人工同步极易漏改,构建时强制校验,漏改直接 fail fast。
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const webuiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const projectRoot = dirname(webuiRoot);

const read = (p) => readFileSync(p, 'utf8').replace(/\r\n/g, '\n').trimEnd();

const rootCopy = read(join(projectRoot, 'sdgun-api-types.ts'));
const localCopy = read(join(webuiRoot, 'src', 'types', 'sdgun-api-types.ts'));

if (rootCopy !== localCopy) {
  console.error('[check-types-sync] 类型双副本不一致,构建中止:');
  console.error('  主文件:   sdgun-api-types.ts');
  console.error('  构建副本: webui/src/types/sdgun-api-types.ts');
  console.error('请把两份文件改成一致后再构建。');
  process.exit(1);
}
console.log('[check-types-sync] 类型双副本一致');
