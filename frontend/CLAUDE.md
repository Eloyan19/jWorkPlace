# CLAUDE.md — frontend/ (TS · React · Vite)

> Родитель: `../CLAUDE.md` (продукт, слои агентов, инварианты grounding/безопасности).
> Этот файл — **только построчные конвенции** TS/React-кода в `frontend/src/`. Референс стека —
> `../../webchat/frontend`. Прозы на русском, идентификаторы/код — латиницей.

## Naming & conventions (по наблюдаемому коду, не выдумывать новое)

- Без точек с запятой, одинарные кавычки, отступ 2 пробела, `import type {…}` для типов-импортов.
- **Все** сетевые вызовы — только в `src/api.ts`. Компонент никогда не зовёт `fetch` напрямую.
- URL — **только относительные** `/api/*`: dev проксирует Vite (`vite.config.ts` →
  `server.proxy['/api']`), прод — nginx. Абсолютный backend-URL нигде не хардкодим.
- Заголовки авторизации — только через `authHeaders()`; текст ошибки — только через
  `readErrorMessage(res)` (401 → отдельное сообщение про `?token=`, иначе `detail` из JSON-тела,
  иначе `Backend error <status>`).
- Доменный отказ с телом (`{ok:false}` при HTTP 409/403 — «превью устарело», «правки выключены»)
  — это **не транспортная ошибка**: проверяем `res.status` явно и разбираем тело наравне с 200,
  не бросаем `Error`. Транспортную ошибку (сеть, 5xx, неожиданный статус) — бросаем.
- Все переиспользуемые типы — в `src/types.ts`. Успех/отказ домена — **дискриминированный union
  по полю `ok`** (не отдельные необязательные поля).
- Компонент — `default export`, **function declaration** (не стрелка, не `React.FC`), PascalCase,
  один файл — один `*Panel`.
- Состояние — только `useState`/`useEffect`/`useRef`/`useCallback`. Без redux/context/router.
- Гонки: `mountedRef` — не звать `setState` после unmount; `<thing>Ref` (напр. `activeIdRef`,
  `refreshRef`) — свежее значение внутри async-хендлера/интервала без устаревшего замыкания.
- Кросс-панельная связь — только через `src/activeProject.ts` (localStorage + `CustomEvent
  'jwp:active-project'`), не через проп-дриллинг и не через контекст.
- Вкладки в `App.tsx` остаются смонтированными (`hidden` вместо unmount) — состояние панели
  (история чата, результат поиска) не теряется при переключении.
- PAT/токен на фронте **никогда** не хранить и не логировать: поле `type="password"`,
  `autoComplete="off"`, значение очищается в `finally` независимо от исхода запроса.
- Тесты — `src/__tests__/<Component>.test.tsx`, vitest + `@testing-library/react`, `vi.mock('../api')`.

## Примеры хорошего кода (реальные, из этой кодовой базы)

```ts
// api.ts::searchCode — относительный URL, authHeaders, readErrorMessage.
// Почему: единая точка сетевого вызова — компонент не знает про fetch/заголовки/парсинг ошибок.
export async function searchCode(projectId: string, query: string, k = 8): Promise<SearchResponse> {
  const res = await fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ project_id: projectId, query, k }),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as SearchResponse
}
```

```ts
// types.ts::EditResponse — дискриминированный union по ok.
// Почему: компонент разбирает результат через if (res.ok) без опциональных полей и ! на типах.
export type EditResponse =
  | { ok: true; summary: string; diff: string; edits: EditItem[]; sources: EditSource[]; dropped: number }
  | { ok: false; reason: string }
```

```ts
// SearchPanel.tsx — mountedRef + <thing>Ref против гонки при переключении проекта.
// Почему: пока запрос летит, пользователь мог сменить activeId — без сверки покажем чужие хиты.
const searchedId = activeId
const res = await searchCode(searchedId, trimmed)
if (mountedRef.current && activeIdRef.current === searchedId) setResult(res)
```

```ts
// SearchPanel.tsx — подписка на activeProject вместо пропа.
// Почему: ProjectsPanel и SearchPanel не знают друг о друге; связь только через событие+localStorage.
useEffect(() => {
  return subscribeActiveProject((id) => {
    setActiveId(id)
    setResult(null)
    setError(null)
  })
}, [])
```

```tsx
// SearchPanel.tsx — объявление компонента.
// Почему: единый стиль (function declaration, не const-стрелка) по всей src/components/.
function SearchPanel() {
  /* … */
}

export default SearchPanel
```

## Антипаттерны (запрещено)

- **`any`** — использовать точный тип или union (`SearchHit | null`, дискриминированный union
  по `ok`). `any` глушит именно те ошибки типов, ради которых здесь TS.
- **`console.log`/`console.error` в проде** — ошибки идут в state (`setError`) и рендерятся
  пользователю; ничего не пишем в консоль браузера сверх того, что уже логирует React/Vite.
- **Абсолютный backend-URL** (`http://127.0.0.1:8200/...` или домен) вместо `/api/*` — ломает
  dev-proxy и прод-nginx одновременно; используем только относительный путь.
- **Прямой `fetch` в компоненте** мимо `api.ts` — теряет `authHeaders`/`readErrorMessage`/типизацию
  ответа и создаёт второй источник правды о контракте backend'а.
- **`setState` после unmount без `mountedRef`** — React предупреждение + возможная перезапись
  состояния уже другого (переключённого) проекта; проверять `mountedRef.current` перед каждым
  `setState` в async-колбэке.
- **Хранение реального PAT** в `localStorage`/state дольше запроса/логах — токен только
  проксируется на backend и сразу стирается из поля ввода, независимо от успеха.

## Шаблон типового компонента

```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import type { Project } from '../types'
import { getProject } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'

function ExamplePanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setProject(null)
      setError(null)
    })
  }, [])

  const load = useCallback(async () => {
    if (!activeId) return
    try {
      const p = await getProject(activeId)
      if (mountedRef.current) setProject(p)
    } catch (err) {
      if (mountedRef.current) setError(err instanceof Error ? err.message : 'ошибка загрузки')
    }
  }, [activeId])

  useEffect(() => {
    load()
  }, [load])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    // вызов api.ts, разбор ok/ошибки, обновление state через mountedRef-гвард
  }

  return (
    <section className="example-panel">
      <h2>Пример</h2>
      {error && <p className="example-error">{error}</p>}
      {/* JSX по данным project */}
    </section>
  )
}

export default ExamplePanel
```
