---
name: jWorkPlace — frontend (TS/React/Vite)
globs: frontend/**
description: Построчные конвенции TS/React (дистиллят frontend/CLAUDE.md).
---

# Frontend (TypeScript/React/Vite)

- **Все fetch — только в `src/api.ts`**; компонент никогда не зовёт `fetch` напрямую.
- URL — **относительные** `/api/*` (dev-проксирует Vite, прод — nginx). Абсолютный backend-URL нигде.
- Заголовки авторизации — только через `authHeaders()`; текст ошибки — через `readErrorMessage(res)`.
- Разделяемые типы — в `src/types.ts`; успех/отказ домена — **дискриминированный union по `ok`**.
- Компонент — `default export`, **function declaration** (не стрелка, не `React.FC`), один файл — один `*Panel`.
- Состояние — `useState`/`useEffect`/`useRef`/`useCallback`. **Без redux/context/router.**
- Гонки: `mountedRef` — не звать `setState` после unmount; `<thing>Ref` — свежее значение в async-хендлере.
- Кросс-панельная связь — через `src/activeProject.ts` (localStorage + `CustomEvent 'jwp:active-project'`).
- **Запрещено**: `any`; `console.log` в проде; прямой `fetch` в компоненте; абсолютный backend-URL;
  хранение реального PAT в state/localStorage дольше запроса.
- Тесты — `src/__tests__/<Component>.test.tsx`, vitest + `vi.mock('../api')`.
