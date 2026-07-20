import { useEffect, useRef, useState, type FormEvent } from 'react'
import { askSupport } from '../api'
import type { SupportResponse } from '../types'

// Ассистент поддержки (Задание 2): глобальная вкладка (не привязана к активному проекту).
// Отвечает по FAQ продукта jWorkPlace; опциональный ticket_id подмешивает контекст обращения
// через MCP. escalate → ответа в документации нет, обращение уходит человеку.
function SupportPanel() {
  const [question, setQuestion] = useState('')
  const [ticketId, setTicketId] = useState('')
  const [asking, setAsking] = useState(false)
  const [result, setResult] = useState<SupportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  async function handleAsk(e: FormEvent) {
    e.preventDefault()
    const trimmed = question.trim()
    if (!trimmed) return
    setAsking(true)
    setError(null)
    try {
      const res = await askSupport(trimmed, ticketId)
      if (mountedRef.current) setResult(res)
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : 'не удалось получить ответ')
        setResult(null)
      }
    } finally {
      if (mountedRef.current) setAsking(false)
    }
  }

  return (
    <section className="support-panel">
      <h2>Поддержка пользователей</h2>
      <p className="support-hint">
        Вопросы о продукте jWorkPlace. Ответы по документации (FAQ); укажите номер тикета, чтобы
        учесть контекст обращения.
      </p>

      <form className="support-form" onSubmit={handleAsk}>
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="почему репозиторий долго индексируется…"
          aria-label="вопрос в поддержку"
          disabled={asking}
        />
        <input
          type="text"
          className="support-ticket"
          value={ticketId}
          onChange={(e) => setTicketId(e.target.value)}
          placeholder="тикет (напр. T-1001)"
          aria-label="номер тикета (необязательно)"
          disabled={asking}
        />
        <button type="submit" disabled={asking || !question.trim()}>
          {asking ? 'отправка…' : 'Спросить'}
        </button>
      </form>

      {error && <p className="support-error">{error}</p>}

      {result && !error && (
        <div className={`support-answer${result.escalate ? ' support-escalate' : ''}`}>
          <p>{result.answer}</p>
          {result.ticket_applied && (
            <p className="support-ticket-note">✓ учтён контекст указанного тикета</p>
          )}
          {result.sources.length > 0 && (
            <ol className="support-sources">
              {result.sources.map((s, i) => (
                <li key={`${s.citation}-${i}`} className="support-source">
                  <div className="support-source-citation">{s.section}</div>
                  <blockquote className="support-source-quote">{s.quote}</blockquote>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  )
}

export default SupportPanel
