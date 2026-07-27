import { useState } from 'react'
import AgentPanel from './AgentPanel'
import EditPanel from './EditPanel'

type EditMode = 'quick' | 'agent'

const EDIT_MODES: { key: EditMode; label: string; hint: string }[] = [
  {
    key: 'quick',
    label: 'Быстрая правка',
    hint: 'Быстрый одношаговый патч по известному месту в коде',
  },
  {
    key: 'agent',
    label: 'Агент по файлам',
    hint: 'Автономный многошаговый агент: сам исследует проект и создаёт файлы',
  },
]

// Вкладка «Правки» объединяет два под-режима с очень разной механикой (одношаговый патч vs
// многошаговый tool-loop) — сегмент-переключатель, а не стопка, чтобы не путать пользователя,
// какая панель сейчас что делает. Обе панели остаются смонтированными (hidden), как и верхние
// вкладки в App.tsx — не теряем превью diff / ход агента при переключении под-режима.
function EditsPanel() {
  const [mode, setMode] = useState<EditMode>('quick')

  return (
    <div className="edits-panel">
      <div className="edit-mode-switch" role="tablist" aria-label="режим правок">
        {EDIT_MODES.map((m) => (
          <div key={m.key} className="edit-mode-item">
            <button
              role="tab"
              aria-selected={mode === m.key}
              className={`edit-mode-btn${mode === m.key ? ' edit-mode-btn-active' : ''}`}
              onClick={() => setMode(m.key)}
            >
              {m.label}
            </button>
            <p className="edit-mode-hint">{m.hint}</p>
          </div>
        ))}
      </div>

      <div role="tabpanel" hidden={mode !== 'quick'}>
        <EditPanel />
      </div>
      <div role="tabpanel" hidden={mode !== 'agent'}>
        <AgentPanel />
      </div>
    </div>
  )
}

export default EditsPanel
