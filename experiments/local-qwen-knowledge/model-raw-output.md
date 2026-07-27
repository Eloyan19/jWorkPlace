# Сырой вывод локальной модели (qwen2.5-coder:3b, jwp-qwen)

> Захват `ollama run` в non-tty: ANSI-коды сняты. Оставшиеся разрывы строк в середине
> строк SQL/JSX — это word-wrap-редроу терминала, не часть кода модели. Смысл читается.
> Это ДОСЛОВНЫЙ вывод модели — код в PR перенесён из него как есть (с её дефектами).

### Изменённый код

#### backend/app/db.py
1. Добавить функцию `mark_concept_known(slug)`:
```python
def mark_concept_known(slug: str) -> None:
    """Пометить концепт с указанным slug известным."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE concepts SET known = 1, known_at = COALESCE(known_at, d
datetime('now')) "
            "WHERE known = 0 AND slug = ?",
            (slug,),
        )
```

2. Добавить функцию `reset_known_catalog()`:
```python
def reset_known_catalog() -> None:
    """Мягкий сброс всех концептов на known=0."""
    with get_conn() as conn:
        conn.execute("UPDATE concepts SET known = 0")
```

#### backend/app/api/knowledge.py
1. Добавить эндпоинт `/api/knowledge/concepts/{slug}/known`:
```python
@router.post("/concepts/{slug}/known")
async def mark_concept_known(slug: str) -> dict:
    """Пометить концепт с указанным slug известным."""
    row = db.get_concept_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Концепт не найден.")
    await asyncio.to_thread(db.mark_concept_known, slug)
    return {"ok": True}
```

2. Добавить эндпоинт `/api/knowledge/concepts/reset`:
```python
@router.post("/concepts/reset")
async def reset_known_catalog() -> dict:
    """Мягкий сброс всех концептов на known=0."""
    await asyncio.to_thread(db.reset_known_catalog)
    return {"ok": True}
```

#### backend/app/knowledge/render.py
1. Прокинуть `slug` в `get_project_concepts`:
```python
def get_project_concepts(project_id: str) -> list[sqlite3.Row]:
    """Концепты проекта + их project-scoped раскрытие, джойном на глобальны
глобальный каталог."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT pc.concept_id AS concept_id, c.name AS name, c.category
c.category AS category, "
            "c.description AS description, c.known AS known, pc.detail AS d
detail, "
            "pc.evidence AS evidence, c.slug AS slug "
            "FROM project_concepts pc JOIN concepts c ON c.id = pc.concept_
pc.concept_id "
            "WHERE pc.project_id = ?",
            (project_id,),
        ).fetchall()
```

2. Прокинуть `slug` в `render.py`:
```python
def render(project_id: str) -> dict:
    summary = db.get_summary(project_id)
    if summary is None:
        return {"status": "error", "reason": "no_summary"}
    new_concepts = []
    known_concepts = []
    for row in db.get_project_concepts(project_id):
        if row["known"]:
            known_concepts.append({"name": row["name"], "slug": row["slug"]
row["slug"]})
            continue
        evidence = []
        if row["evidence"]:
            try:
                evidence = json.loads(row["evidence"])
            except (json.JSONDecodeError, TypeError):
                evidence = []
        new_concepts.append({"name": row["name"], "detail": row["detail"], 
"evidence": evidence, "slug": row["slug"]})
    ...
    return {"status": "ready", "overview": ..., "tech": ..., "concepts": {"
{"new": new_concepts, "known": known_concepts}}
```

#### frontend/src/api.ts
1. Добавить функцию `markConceptKnown(slug: string): Promise<{ ok: true }>`
}>`:
```typescript
export async function markConceptKnown(slug: string): Promise<{ ok: true }>
}> {
  const res = await fetch(`/api/knowledge/concepts/${slug}/known`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as { ok: true }
}
```

2. Добавить функцию `resetKnownCatalog(): Promise<{ ok: true }>`:
```typescript
export async function resetKnownCatalog(): Promise<{ ok: true }> {
  const res = await fetch(`/api/knowledge/concepts/reset`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as { ok: true }
}
```

#### frontend/src/types.ts
1. Добавить поле `slug` в `ConceptDetail`:
```typescript
export interface ConceptDetail {
  name: string
  detail: string
  evidence: ConceptEvidence[]
  slug: string
}
```

2. Добавить поле `slug` в `ConceptEvidence` (если необходимо):
```typescript
export interface ConceptEvidence {
  // ... другие поля ...
  slug: string
}
```

#### frontend/src/components/SummaryPanel.tsx
1. Добавить кнопку «Очистить базу знаний»:
```tsx
import { resetKnownCatalog } from '../api'
// ... state: activeId, summary, expandedNew (Set<string>), resetKnownRef .
...
const loadSummary = useCallback(async () => {
  const res = await getSummary(loadedId)
  setSummary(res)
  if (res.status === 'ready' && res.concepts.new.length > 0 && markedRef.cu
markedRef.current !== loadedId) {
    markedRef.current = loadedId
    markSummaryRead(loadedId).catch(() => { markedRef.current = null })
  }
}, [activeId])

const toggleNew = useCallback((name: string) => {
  setExpandedNew((prev) => { const next = new Set(prev); next.has(name) ? n
next.delete(name) : next.add(name); return next })
}, [])

const handleResetKnown = useCallback(async () => {
  if (window.confirm('Вы действительно хотите очистить базу знаний?')) {
    await resetKnownCatalog()
    // Перезагрузить выжимку
    window.location.reload()
  }
}, [])

// render:
<h2>О проекте</h2>
// список новых:
{summary.concepts.new.map((c) => {
  const isOpen = expandedNew.has(c.name)
  return (
    <li key={c.name} className="summary-concept">
      <button className="summary-concept-toggle" onClick={() => toggleNew(c
toggleNew(c.name)} aria-expanded={isOpen}>
        <span className="tree-caret">{isOpen ? '▾' : '▸'}</span>{c.name}
      </button>
      {isOpen && (<div className="summary-concept-detail"><p>{c.detail}</p>
className="summary-concept-detail"><p>{c.detail}</p>...</div>)}
    </li>
  )
})}
// список знакомых:
{summary.concepts.known.map((k) => (<li key={k.name} className="summary-chi
className="summary-chip summary-chip-known">{k.name}</li>))}
// кнопка «Очистить базу знаний»:
<button onClick={handleResetKnown}>Очистить базу знаний</button>
```

### Описание изменений
1. **Backend:**
   - Добавлены функции `mark_concept_known(slug)` и `reset_known_catalog()`
`reset_known_catalog()` для управления состоянием концептов.
   - Добавлены эндпоинты `/api/knowledge/concepts/{slug}/known` и `/api/kno
`/api/knowledge/concepts/reset`.

2. **Frontend:**
   - Добавлена функция `markConceptKnown(slug: string): Promise<{ ok: true 
}>` для пометки концепта как изученного.
   - Добавлена функция `resetKnownCatalog(): Promise<{ ok: true }>` для сбр
сброса состояния всех концептов на известные.
   - Добавлена кнопка «Очистить базу знаний» в компоненте `SummaryPanel`, к
которая вызывает `resetKnownCatalog()` и перезагружает страницу.

