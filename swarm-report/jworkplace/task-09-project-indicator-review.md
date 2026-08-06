PASS

# Review — T09 индикатор активного проекта (ветка jworkplace/task-09-project-indicator)

Ревьюер: `code-reviewer` (TS/React). Прогнан App.test.tsx (22/22).

## Вердикт: PASS

- **Race-guard корректен:** `activeIdRef` пишется в теле рендера, устаревший `getProject`-ответ
  отбрасывается сверкой в `.then` (ActiveProjectIndicator.tsx:38); `mountedRef` защищает от setState
  после unmount.
- **Отписка корректна:** cleanup эффекта возвращает `unsubscribe` от `subscribeActiveProject` — утечки нет.
- **Условие рендера верно:** индикатор вне `.tab-pane` → инвариант mounted-panels не затронут; двойная
  защита (`tab !== SUPPORT_TAB.key` + `if(!activeId) return null`) не даёт показать на Support/без проекта.
- **Fallback `name ?? activeId`** — всегда строка.
- T01/T07/T08 не задеты; тесты не тавтологичны.

## Nit (low, не блокер, принято оставить для MVP)
При переключении Support↔проектная вкладка индикатор ремаунтится → имя на миг мигает `null→id→name`
(повторный `getProject`). Чистый UX-flash, не баг. Можно later устранить (держать смонтированным +
скрывать через CSS / мемоизировать). Для MVP приемлемо.
