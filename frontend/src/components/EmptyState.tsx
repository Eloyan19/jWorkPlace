interface EmptyStateProps {
  message: string
  hint?: string
}

// Единая заглушка для проектных вкладок, пока активный проект не выбран (см. App.tsx).
// Раньше каждая панель рисовала свой собственный текст-подсказку — здесь один компонент,
// одно сообщение, никакой рассинхронизации формулировок между вкладками.
function EmptyState({ message, hint }: EmptyStateProps) {
  return (
    <section className="empty-state">
      <p className="empty-state-message">{message}</p>
      {hint && <p className="empty-state-hint">{hint}</p>}
    </section>
  )
}

export default EmptyState
