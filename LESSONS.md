# Runtime Lessons

## 2026-08-18 - Test fixtures must not inherit evolving source config

- Build the smallest source dictionaries inside tests that exercise legacy `llm_sweep` behavior.
- Do not use `config.sources[:N]` or mutate production adapter entries into a different adapter type.
- When the production source schema changes (`url` to `urls`, adapter type, selectors), adapter-specific tests own their own explicit fixture schema.
