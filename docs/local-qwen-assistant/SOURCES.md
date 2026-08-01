# Источники (сверено 2026-07)

Факты гайда сверены по официальной документации и первоисточникам:

**Контекст Ollama (num_ctx по умолчанию мал → поднимать явно):**
- https://docs.ollama.com/ — `OLLAMA_CONTEXT_LENGTH`, `PARAMETER num_ctx`, per-request `options.num_ctx`
- https://www.autodidacts.io/increase-ollama-context-length-num-ctx/
- https://github.com/microsoft/autogen/discussions/5254

**qwen2.5-coder (контекст 32K→128K, FIM-токены, параметры):**
- https://github.com/QwenLM/Qwen2.5-Coder — репо модели
- https://arxiv.org/html/2409.12186v2 — Qwen2.5-Coder Technical Report (32K repo-level, FIM)
- https://deepwiki.com/QwenLM/Qwen2.5-Coder/2.2-fill-in-the-middle — FIM-токены `<|fim_prefix/middle/suffix/pad|>`
- https://qwen2.org/qwen2-5-coder/

**Continue (rules-файлы .continue/rules/, config.yaml):**
- https://docs.continue.dev/customize/deep-dives/rules
- https://docs.continue.dev/reference — config.yaml reference
- https://docs.continue.dev/customize/deep-dives/configuration

**Twinny:** маркетплейс VS Code (расширение `rjmacarthy.twinny`), Ollama-native, FIM+chat.
