import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { getProject, sendChat } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { ChatMessage, ChatSource, Project } from '../types'

const POLL_INTERVAL_MS = 2_000

// Сообщение для отображения в ленте: несёт доп. поля источников/abstain, которых нет в
// «сыром» ChatMessage (тот уходит на backend без них — см. handleSend).
interface DisplayMessage extends ChatMessage {
  sources?: ChatSource[]
  abstain?: boolean
}

function ChatPanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Свежий activeId для сверки в асинхронном handleSend (защита от гонки при переключении).
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  // Смена активного проекта в ProjectsPanel → диалог прошлого проекта не переносим
  // (контексты проектов не смешиваем).
  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setMessages([])
      setError(null)
    })
  }, [])

  // Детали активного проекта (имя/статус). Пока не ready — поллим, чтобы поймать переход в ready.
  const loadProject = useCallback(async () => {
    if (!activeId) {
      setProject(null)
      return
    }
    try {
      const p = await getProject(activeId)
      if (mountedRef.current) setProject(p)
    } catch {
      if (mountedRef.current) setProject(null)
    }
  }, [activeId])

  useEffect(() => {
    loadProject()
  }, [loadProject])

  const notReady = project !== null && project.status !== 'ready'
  useEffect(() => {
    if (!notReady) return
    const timer = setInterval(loadProject, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [notReady, loadProject])

  async function handleSend(e: FormEvent) {
    e.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || !activeId) return
    const sentId = activeId
    const userMsg: DisplayMessage = { role: 'user', content: trimmed }
    const history = messages.concat(userMsg)
    setMessages(history)
    setInput('')
    setSending(true)
    setError(null)
    try {
      // На backend уходят только role/content — history-объекты во фронте несут доп. поля
      // (sources/abstain) только для рендера, в тело запроса их не тащим.
      const payload: ChatMessage[] = history.map(({ role, content }) => ({ role, content }))
      const res = await sendChat(sentId, payload)
      // Игнорируем ответ, если проект переключили, пока запрос летел (иначе покажем чужой ответ).
      if (mountedRef.current && activeIdRef.current === sentId) {
        setMessages((prev) =>
          prev.concat({
            role: 'assistant',
            content: res.answer,
            sources: res.sources,
            abstain: res.abstain,
          }),
        )
      }
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === sentId) {
        setError(err instanceof Error ? err.message : 'не удалось получить ответ')
      }
    } finally {
      if (mountedRef.current) setSending(false)
    }
  }

  return (
    <section className="chat-panel">
      <h2>Чат по коду</h2>

      {!activeId ? (
        <p className="chat-hint">выберите готовый проект в списке выше</p>
      ) : notReady ? (
        <p className="chat-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : (
        <>
          {messages.length > 0 && (
            <ol className="chat-messages">
              {messages.map((m, i) => (
                <ChatBubble key={i} message={m} />
              ))}
            </ol>
          )}

          {sending && <p className="chat-typing">печатает…</p>}
          {error && <p className="chat-error">{error}</p>}

          <form className="chat-form" onSubmit={handleSend}>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="что делает проект, где вызывается Y…"
              aria-label="вопрос по коду проекта"
              disabled={sending}
            />
            <button type="submit" disabled={sending || !input.trim()}>
              {sending ? 'отправка…' : 'Спросить'}
            </button>
          </form>
        </>
      )}
    </section>
  )
}

function ChatBubble({ message }: { message: DisplayMessage }) {
  if (message.role === 'user') {
    return (
      <li className="chat-bubble chat-bubble-user">
        <p>{message.content}</p>
      </li>
    )
  }

  if (message.abstain) {
    return (
      <li className="chat-bubble chat-bubble-assistant chat-abstain">
        <p>{message.content || 'не знаю, уточните вопрос.'}</p>
      </li>
    )
  }

  return (
    <li className="chat-bubble chat-bubble-assistant">
      <p>{message.content}</p>
      {message.sources && message.sources.length > 0 && (
        <ol className="chat-sources">
          {message.sources.map((s) => (
            <li key={s.id} className="chat-source">
              <div className="chat-source-citation">{s.citation}</div>
              <pre className="chat-source-quote">
                <code>{s.quote}</code>
              </pre>
            </li>
          ))}
        </ol>
      )}
    </li>
  )
}

export default ChatPanel
