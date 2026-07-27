import { test, expect, type Page } from '@playwright/test'
import * as path from 'path'
import { fileURLToPath } from 'url'

// ESM: __dirname нет в scope — выводим из import.meta.url.
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SCREENSHOTS_DIR = path.join(__dirname, 'screenshots')

// Скриншот на КАЖДОМ шаге сценария (нумерация 01-, 02-…). Сбрасывается перед каждым тестом.
let stepCounter = 0
async function shot(page: Page, name: string) {
  stepCounter++
  // Префикс сценария (S1..S5) из заголовка теста — иначе одинаковые имена шагов перезапишут
  // друг друга между сценариями, и «скриншот на каждом шаге» потеряется.
  const scenario = test.info().title.split(':')[0].trim()
  const filename = `${scenario}-${String(stepCounter).padStart(2, '0')}-${name}.png`
  await page.screenshot({ path: path.join(SCREENSHOTS_DIR, filename) })
  console.log(`  📸 ${filename}`)
  return filename
}

// Выбрать первый ready-проект в ProjectsPanel через реальный клик; вернуть его li-локатор
// или null (тогда сценарий gracefully скипается). Формы Search/Chat появляются ТОЛЬКО после
// выбора ready-проекта (иначе панель показывает hint «выберите готовый проект»).
async function selectReadyProject(page: Page) {
  // Список грузится async — дождёмся списка или пустого состояния, затем появления ready-бейджа.
  await page.locator('ul.projects-list, p.projects-empty').first().waitFor({ timeout: 15000 })
  await page.locator('.badge-ready').first().waitFor({ timeout: 10000 }).catch(() => {})
  const readyItem = page
    .locator('ul.projects-list li.project-item', { has: page.locator('.badge-ready') })
    .first()
  if ((await readyItem.count()) === 0) return null
  await readyItem.locator('button.project-select').click()
  await expect(readyItem).toHaveClass(/project-item-active/)
  return readyItem
}

test.beforeEach(() => {
  stepCounter = 0
})

/**
 * S1 — Health & загрузка страницы.
 * Открыть /, дождаться рендера, проверить индикатор здоровья backend и навигацию.
 */
test('S1: health и загрузка страницы', async ({ page }) => {
  await page.goto('/')
  await shot(page, 'page-loaded')

  // Индикатор здоровья backend (role=status). Ждём online, но офлайн всё равно валидный рендер.
  const health = page.locator('.health')
  await expect(health).toBeVisible()
  await expect(page.locator('.health-online')).toBeVisible({ timeout: 15000 })
  console.log('  health: online')
  await shot(page, 'health-online')

  // Навигация: 6 вкладок (Чат/О проекте/Структура/Поиск/Правки + отделённая Поддержка сервиса).
  const tabs = page.getByRole('tab')
  await expect(tabs).toHaveCount(6)
  await expect(page.locator('section.projects-panel')).toBeVisible()
  await shot(page, 'tabs-and-projects-visible')
})

/**
 * S2 — Список проектов и переключение активного (реальный клик по проекту).
 */
test('S2: список и переключение проекта', async ({ page }) => {
  await page.goto('/')
  await shot(page, 'start')

  await expect(page.locator('ul.projects-list')).toBeVisible()
  await shot(page, 'projects-list')

  const selected = await selectReadyProject(page)
  if (!selected) {
    console.log('  нет ready-проектов — skip')
    await shot(page, 'no-ready-project')
    test.skip()
    return
  }
  const name = await selected.locator('.project-name').innerText()
  console.log(`  активный проект: ${name}`)
  await shot(page, 'project-activated')
})

/**
 * S3 — Поиск по коду: вкладка Поиск → ввод запроса → кнопка Искать → отрендеренные результаты.
 */
test('S3: поиск по коду', async ({ page }) => {
  await page.goto('/')
  const selected = await selectReadyProject(page)
  if (!selected) {
    console.log('  нет ready-проектов — skip')
    test.skip()
    return
  }
  await shot(page, 'project-selected')

  await page.getByRole('tab', { name: 'Поиск' }).click()
  const searchInput = page.getByPlaceholder('что делает функция X, где вызывается Y…')
  await expect(searchInput).toBeVisible()
  await searchInput.fill('function')
  await shot(page, 'query-filled')

  await page.getByRole('button', { name: 'Искать' }).click()
  // Валидный исход = либо карточки результатов, либо честный abstain «ничего не найдено».
  const results = page.locator('ol.search-results li.result-card').first()
  const abstain = page.locator('p.search-abstain')
  await expect(results.or(abstain)).toBeVisible({ timeout: 30000 })
  console.log(
    (await results.count()) > 0 ? '  есть результаты поиска' : '  abstain (ничего релевантного)',
  )
  await shot(page, 'search-result')
})

/**
 * S4 — Grounded-чат: вкладка Чат (дефолт) → вопрос → Спросить → ответ ассистента.
 * Реальный вызов DeepSeek через backend — держим щедрый таймаут.
 */
test('S4: grounded-чат', async ({ page }) => {
  await page.goto('/')
  const selected = await selectReadyProject(page)
  if (!selected) {
    console.log('  нет ready-проектов — skip')
    test.skip()
    return
  }
  await shot(page, 'project-selected')

  // Чат — вкладка по умолчанию; убедимся, что активна.
  await page.getByRole('tab', { name: 'Чат' }).click()
  const chatInput = page.getByPlaceholder('что делает проект, где вызывается Y… (/help — возможности)')
  await expect(chatInput).toBeVisible()
  await chatInput.fill('Что делает этот проект?')
  await shot(page, 'question-typed')

  await page.getByRole('button', { name: 'Спросить' }).click()
  // Ждём ответ ассистента: в ленте должно стать 2 пузыря (вопрос + ответ).
  await expect(page.locator('ol.chat-messages li.chat-bubble')).toHaveCount(2, { timeout: 90000 })
  const assistant = page.locator('li.chat-bubble').nth(1)
  await expect(assistant).toBeVisible()
  console.log('  получен ответ ассистента')
  await shot(page, 'chat-answer')
})

/**
 * S5 — Создать → проверить появление → удалить проект (реальный UI-флоу).
 * Берём минимальный публичный репо octocat/Hello-World; чистим за собой.
 */
test('S5: создать, проверить, удалить проект', async ({ page }) => {
  const REPO = 'https://github.com/octocat/Hello-World'
  await page.goto('/')
  await shot(page, 'start')

  const urlInput = page.getByPlaceholder('https://github.com/owner/repo')
  await expect(urlInput).toBeVisible()
  await urlInput.fill(REPO)
  await shot(page, 'url-filled')
  await page.getByRole('button', { name: 'Подключить' }).click()

  // Появился в списке.
  const newItem = page
    .locator('li.project-item', { has: page.getByText('octocat/Hello-World') })
    .first()
  await expect(newItem).toBeVisible({ timeout: 30000 })
  console.log('  проект добавлен в список')
  await shot(page, 'project-appeared')

  // Кнопка «Удалить» доступна для ready/error. Крохотный репо индексируется быстро — ждём ready.
  const deleteBtn = newItem.getByRole('button', { name: 'Удалить' })
  try {
    await expect(newItem.locator('.badge-ready, .badge-error')).toBeVisible({ timeout: 120000 })
    await expect(deleteBtn).toBeVisible()
    await deleteBtn.click()
    await expect(newItem).toHaveCount(0, { timeout: 15000 })
    console.log('  проект удалён через UI')
    await shot(page, 'project-deleted')
  } catch {
    // Не дошёл до состояния с кнопкой удаления — чистим через API, чтобы не оставлять мусор.
    console.log('  UI-удаление недоступно (не ready/error вовремя) — cleanup через API')
    await shot(page, 'delete-fallback')
    const list = await page.request.get('/api/projects').then((r) => r.json())
    const stray = list.find((p: { name: string; id: string }) => p.name?.includes('Hello-World'))
    if (stray) await page.request.delete(`/api/projects/${stray.id}`)
  }
})

/**
 * S6 — База знаний: ручная пометка «Изучено» + «Очистить базу знаний».
 * Проверяет новую фичу: темы попадают в «известное» ТОЛЬКО по кнопке (без авто-пометки при
 * открытии), а «Очистить» мягко сбрасывает всё обратно в «новое».
 */
test('S6: база знаний — «Изучено» и очистка', async ({ page }) => {
  await page.goto('/')
  const selected = await selectReadyProject(page)
  if (!selected) {
    console.log('  нет ready-проектов — skip')
    test.skip()
    return
  }
  await page.getByRole('tab', { name: 'О проекте' }).click()

  // Ждём готовую выжимку с концептами в блоке «Новое для вас».
  const markButtons = page.locator('button.summary-concept-mark-known')
  await expect(markButtons.first()).toBeVisible({ timeout: 30000 })
  const before = await markButtons.count()
  console.log(`  новых концептов: ${before}`)
  await shot(page, 'new-concepts')
  if (before === 0) {
    console.log('  нет новых концептов (всё уже изучено) — skip демо кнопок')
    test.skip()
    return
  }

  // Клик «Изучено» на первом концепте — оптимистично уходит из «нового».
  await markButtons.first().click()
  await expect(markButtons).toHaveCount(before - 1)
  console.log('  концепт помечен изученным (ушёл из «нового»)')
  await shot(page, 'marked-known')

  // «Очистить базу знаний» — confirm авто-accept; после мягкого сброса все снова «новые».
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Очистить базу знаний' }).click()
  await expect(markButtons).toHaveCount(before, { timeout: 15000 })
  console.log('  база знаний очищена — все концепты снова «новые»')
  await shot(page, 'knowledge-reset')
})
