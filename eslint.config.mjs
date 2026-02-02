/* eslint-disable @typescript-eslint/naming-convention */
/*
 * Copyright (C) 2024 AudioCodes Ltd.
 */
import stylistic from '@stylistic/eslint-plugin';
import globals from 'globals';
import tsParser from '@typescript-eslint/parser';
import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import { FlatCompat } from '@eslint/eslintrc';

const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
});

export default tseslint.config(
  {
    languageOptions: {
      globals: {
        ...globals.node,
        globalThis: 'readonly',
        NodeJS: 'readonly'
      },

      parser: tsParser,
      ecmaVersion: 2023,
      sourceType: 'module',

      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname
      }
    },
    linterOptions: {
      reportUnusedDisableDirectives: true
    }
  },
  {
    ignores: [
      'e2e/',
      'dist/',
      'node_modules/'
    ]
  },
  {
    files: ['**/*.ts']
  },
  eslint.configs.recommended,
  ...compat.plugins('require-extensions'),
  ...compat.extends('plugin:require-extensions/recommended'),
  ...tseslint.configs.recommendedTypeChecked, {
  plugins: {
    '@stylistic': stylistic
  }
});
