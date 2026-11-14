import prettier from 'eslint-config-prettier';
import js from '@eslint/js';
import { includeIgnoreFile } from '@eslint/compat';
import sonarjs from 'eslint-plugin-sonarjs';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import { fileURLToPath } from 'node:url';
import ts from 'typescript-eslint';
import svelteConfig from './svelte.config.js';

const gitignorePath = fileURLToPath(new URL('../.gitignore', import.meta.url));

export default ts.config(
  includeIgnoreFile(gitignorePath),
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  prettier,
  ...svelte.configs.prettier,
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.node }
    },
    rules: {
        'no-undef': 'off',
        '@typescript-eslint/no-unused-vars': [
            'warn', // or "error"
            {
                'argsIgnorePattern': '^_',
                'varsIgnorePattern': '^_',
                'caughtErrorsIgnorePattern': '^_'
            }
        ]
    }
  },
  // Every `target="_blank"` carries `rel="noopener"`, and one that leaves apache.org
  // carries `rel="noopener noreferrer"` as well. Referrer stripping costs nothing on a
  // link to apache.org or to this tool itself, and the destination knowing which page it
  // was clicked from is not something to hand to a host we do not run - which is mostly
  // a url that came out of a document's text, and could be anywhere.
  //
  // `allowReferrer` is what lets both spellings pass: without it the rule demands
  // `noreferrer` on every one. So this gates the `noopener` half only; which links also
  // want `noreferrer` is a judgement about the destination that no rule can make.
  //
  // Nor does it see every link: an anchor built as an HTML string rather than as markup
  // is invisible to it (BAT's issue #698).
  {
    rules: {
      'svelte/no-target-blank': ['error', { allowReferrer: true }]
    }
  },
  // Complexity is reported as a warning rather than an error, so `yarn lint` stays a
  // gate on correctness while the count of these is what scripts/ci/quality.py ratchets
  // down. An editor still shows them the moment the function grows.
  {
    plugins: { sonarjs },
    rules: {
      // Branch count. The bar the codebase is held to, not the bar it meets today.
      complexity: ['warn', 12],
      // How hard the function is to hold in your head: nesting counts against you and a
      // flat switch does not, which is the half `complexity` above is blind to.
      'sonarjs/cognitive-complexity': ['warn', 15]
    }
  },
  {
    files: ['**/*.svelte', '**/*.svelte.ts', '**/*.svelte.js'],
    languageOptions: {
      parserOptions: {
        projectService: true,
        extraFileExtensions: ['.svelte'],
        parser: ts.parser,
        svelteConfig
      }
    }
  }
);
