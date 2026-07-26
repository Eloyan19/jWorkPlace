# Level 2 — Playwright UI Smoke: текстовые сценарии

**Дата:** 2026-07-26 · **Стенд:** локальный dev (`http://localhost:5173`, Vite → backend `:8200`)
**Запуск:** `cd frontend && npx playwright test` (Playwright сам поднимает `npm run dev`)
**Фактический результат последнего прогона: 🟢 5/5 PASS, 16 скриншотов** (`frontend/e2e/screenshots/`, префикс `S1..S5`).

> Прод `jwork.jorchik.com` `/api/*` под Bearer (401) → не цель смоука. Локальный `:8200` открыт и держит
> актуальную сборку + ready-проекты (`AlexGladkov/harnest`, `AlexGladkov/claude-code-agents`, `Eloyan19/jWorkPlace`).
> Все сценарии — **реальные клики/ввод по UI** (`getByRole`/`getByPlaceholder`, `.click()`, `.fill()`), не fetch.

---

## S1 — Health & загрузка страницы  🟢 PASS
Пользователь открывает приложение и видит, что оно живо.
1. `goto('/')` → страница отрисована. `S1-01-page-loaded.png`
2. Индикатор здоровья backend перешёл в `online` (`.health-online`). `S1-02-health-online.png`
3. Видны 6 вкладок (Чат/О проекте/Структура/Поиск/Правки/Поддержка) + панель проектов. `S1-03-tabs-and-projects-visible.png`
- **Assertion:** `.health-online` виден; `getByRole('tab')` = 6; `section.projects-panel` виден.

## S2 — Список и переключение проекта  🟢 PASS
Пользователь выбирает проект в списке — он становится активным.
1. Список проектов отрисован (`ul.projects-list`). `S2-01-start.png`, `S2-02-projects-list.png`
2. Клик по кнопке ready-проекта (`li.project-item` c `.badge-ready` → `button.project-select`).
3. Проект помечен активным (`li.project-item-active`). `S2-03-project-activated.png`
- **Assertion:** выбранный `li` получает класс `project-item-active`. (В прогоне активен `AlexGladkov/harnest`.)

## S3 — Поиск по коду  🟢 PASS
Пользователь ищет по коду активного проекта.
1. Выбран ready-проект (формы Search/Chat появляются только после этого). `S3-01-project-selected.png`
2. Вкладка «Поиск» → ввод «function» в поле (`placeholder="что делает функция X…"`). `S3-02-query-filled.png`
3. Кнопка «Искать» → отрисованы карточки результатов (`ol.search-results li.result-card`) **или** честный abstain (`p.search-abstain`). `S3-03-search-result.png`
- **Assertion:** виден блок результатов ИЛИ abstain (оба валидны — abstain = гейт grounding). В прогоне — есть результаты.

## S4 — Grounded-чат  🟢 PASS
Пользователь спрашивает про проект и получает обоснованный ответ.
1. Выбран ready-проект. `S4-01-project-selected.png`
2. Вкладка «Чат» → вопрос «Что делает этот проект?» (`placeholder="что делает проект…"`). `S4-02-question-typed.png`
3. Кнопка «Спросить» → в ленте появляется ответ ассистента (`ol.chat-messages` → 2 пузыря). `S4-03-chat-answer.png`
- **Assertion:** `li.chat-bubble` = 2 (вопрос + ответ), таймаут 90s (реальный вызов DeepSeek). В прогоне ответ по `harnest`.

## S5 — Создать → проверить → удалить проект  🟢 PASS (с оговоркой)
Аналог «создать сущность → проверить появление → удалить» (в jWorkPlace нет логина — single-user).
1. Форма подключения: ввод `https://github.com/octocat/Hello-World` (минимальный репо). `S5-01-start.png`, `S5-02-url-filled.png`
2. Кнопка «Подключить» → проект появился в списке. `S5-03-project-appeared.png`
3. Удаление: кнопка «Удалить» (рендерится только для `ready`/`error`, `ProjectsPanel.tsx:303`). `S5-04-*.png`
- **Оговорка (находка):** в прогоне репо не дошёл до `ready`/`error` в окне ожидания → кнопки «Удалить» не было →
  сработал **API-fallback** `DELETE /api/projects/{id}` (мусора не осталось). UI-путь удаления только что
  созданного проекта требует отдельной проверки (см. `master-qa-report.md` → «Находки»).

---

## Справочник селекторов (актуальные, по разметке компонентов)
| Элемент | Локатор |
|---|---|
| Вкладки | `getByRole('tab', { name: 'Поиск' \| 'Чат' \| … })` |
| Панель проектов | `section.projects-panel` |
| ready-проект | `li.project-item` c `.badge-ready` → `button.project-select` |
| Активный проект | `li.project-item-active` |
| Поиск: поле / кнопка | `getByPlaceholder('что делает функция X…')` / `getByRole('button',{name:'Искать'})` |
| Результаты поиска | `ol.search-results li.result-card` \| `p.search-abstain` |
| Чат: поле / кнопка | `getByPlaceholder('что делает проект…')` / `getByRole('button',{name:'Спросить'})` |
| Пузыри чата | `ol.chat-messages li.chat-bubble` |
| Подключить репо | `getByPlaceholder('https://github.com/owner/repo')` → `getByRole('button',{name:'Подключить'})` |
| Удалить проект | `li…getByRole('button',{name:'Удалить'})` (только для ready/error) |

## Место в flow
- После PR: hook `.claude/hooks/post-pr-smoke.sh` (срабатывает на `gh pr create`) напоминает запустить `/post-pr-check`.
- Обновить smoke под задеплоенную фичу и прогнать всё: `/post-pr-check --refresh`.
- Сводный отчёт обоих уровней: `swarm-report/<branch>-qa-report.md` (шаблон `TEMPLATE-qa-report.md`).
