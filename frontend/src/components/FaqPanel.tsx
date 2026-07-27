import PanelHeader from './PanelHeader'

// Статическая справка о сервисе (не о подключённом репозитории) — без api-вызовов и без
// зависимости от активного проекта. Тексты держим в одном месте, чтобы менять их вместе
// с реальным поведением панелей, а не гадать по коду каждой из них.
interface FaqMode {
  name: string
  description: string
}

const MODES: FaqMode[] = [
  {
    name: 'Чат',
    description:
      'Спросите про код проекта — ассистент отвечает связным текстом с цитатами из файлов ' +
      '(grounded); если оснований в коде нет, честно отвечает «не знаю».',
  },
  {
    name: 'О проекте',
    description:
      'Автоматическая выжимка о репозитории: обзор, технологии, ключевые концепты. Концепты ' +
      'можно помечать «изучено».',
  },
  {
    name: 'Структура',
    description: 'Дерево файлов и символов проекта для навигации — без участия LLM.',
  },
  {
    name: 'Поиск',
    description:
      'Находит релевантные фрагменты кода со скорами (гибридный поиск): показывает, где именно ' +
      'в коде, — без генерации ответа.',
  },
  {
    name: 'Правки → Быстрая правка',
    description: 'Одношаговый патч по известному месту → превью diff → Pull Request.',
  },
  {
    name: 'Правки → Агент по файлам',
    description:
      'Автономный многошаговый агент: сам исследует проект, создаёт и меняет файлы, затем ' +
      'открывает Pull Request.',
  },
  {
    name: 'Поддержка сервиса',
    description:
      'Вопросы о самом jWorkPlace — как пользоваться, какие есть возможности, — не о вашем ' +
      'репозитории.',
  },
]

function FaqPanel() {
  return (
    <section className="faq-panel">
      <PanelHeader title="Справка" subtitle="Что делает каждая вкладка сервиса" />

      <dl className="faq-list">
        {MODES.map((m) => (
          <div key={m.name} className="faq-item">
            <dt className="faq-name">{m.name}</dt>
            <dd className="faq-desc">{m.description}</dd>
          </div>
        ))}
      </dl>

      <div className="faq-callout">
        <p className="faq-callout-title">Чат vs Поиск</p>
        <p className="faq-callout-text">
          <strong>Поиск</strong> — «где это в коде»: список мест в файлах с оценками релевантности,
          без генерации ответа. <strong>Чат</strong> — «дай ответ»: связный текст LLM поверх
          найденного, с цитатами.
        </p>
      </div>
    </section>
  )
}

export default FaqPanel
