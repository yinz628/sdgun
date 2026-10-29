import { defineConfig } from 'vitest/config';

/**
 * 前端单元测试配置。
 * 默认环境为 node;需要 DOM/localStorage 的用例在文件头用
 * `// @vitest-environment jsdom` 单独覆盖(见 utils/__tests__/notify.test.ts)。
 */
export default defineConfig({
  test: {
    environment: 'node',
  },
});
