export default [
  {
    ignores: ["dist/**", "node_modules/**", "src/**/*.ts", "src/**/*.tsx", "../sandbox/frontend/**/*.ts", "../sandbox/frontend/**/*.tsx"],
  },
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
    },
    rules: {
      "no-constant-condition": "error",
      "no-debugger": "error",
      "no-dupe-keys": "error",
      "no-unreachable": "error",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
];
