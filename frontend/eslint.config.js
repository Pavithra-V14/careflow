import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist/', 'dist-e2e/', 'node_modules/', 'src/shared/api/schema.d.ts', 'playwright-report/', 'test-results/'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  reactHooks.configs.flat.recommended,
  {
    files: ['scripts/**/*.mjs', '*.config.js'],
    languageOptions: { globals: { process: 'readonly', console: 'readonly' } },
  },
);
