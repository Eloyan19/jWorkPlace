import { useCallback, useEffect, useRef, useState } from 'react'
import { getProject, getStructure } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { FileNode, Project, ProjectStructure } from '../types'

const POLL_INTERVAL_MS = 2_000
const INDENT_REM = 1.05

// --- дерево из плоских путей ---
interface TreeDir {
  type: 'dir'
  name: string
  path: string
  children: TreeNode[]
}
interface TreeFile {
  type: 'file'
  name: string
  path: string
  file: FileNode
}
type TreeNode = TreeDir | TreeFile

function buildTree(files: FileNode[]): TreeNode[] {
  const root: TreeDir = { type: 'dir', name: '', path: '', children: [] }
  for (const f of files) {
    const parts = f.path.split('/')
    let cur = root
    for (let i = 0; i < parts.length - 1; i++) {
      const name = parts[i]
      const path = parts.slice(0, i + 1).join('/')
      let next = cur.children.find((c) => c.type === 'dir' && c.name === name) as TreeDir | undefined
      if (!next) {
        next = { type: 'dir', name, path, children: [] }
        cur.children.push(next)
      }
      cur = next
    }
    cur.children.push({ type: 'file', name: parts[parts.length - 1], path: f.path, file: f })
  }
  sortDir(root)
  return root.children
}

function sortDir(dir: TreeDir): void {
  dir.children.sort((a, b) =>
    a.type !== b.type ? (a.type === 'dir' ? -1 : 1) : a.name.localeCompare(b.name),
  )
  for (const c of dir.children) if (c.type === 'dir') sortDir(c)
}

// Все пути каталогов (для «развернуть всё» и стартового состояния — папки раскрыты, файлы свёрнуты).
function allDirPaths(files: FileNode[]): string[] {
  const set = new Set<string>()
  for (const f of files) {
    const parts = f.path.split('/')
    for (let i = 0; i < parts.length - 1; i++) set.add(parts.slice(0, i + 1).join('/'))
  }
  return [...set]
}

function StructurePanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [structure, setStructure] = useState<ProjectStructure | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setStructure(null)
      setExpanded(new Set())
      setError(null)
    })
  }, [])

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

  async function handleLoad() {
    if (!activeId) return
    const loadedId = activeId
    setLoading(true)
    setError(null)
    try {
      const res = await getStructure(loadedId)
      if (mountedRef.current && activeIdRef.current === loadedId) {
        setStructure(res)
        setExpanded(new Set(allDirPaths(res.files))) // старт: папки раскрыты, символы свёрнуты
      }
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === loadedId) {
        setError(err instanceof Error ? err.message : 'не удалось загрузить структуру')
        setStructure(null)
      }
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }

  const toggle = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(path) ? next.delete(path) : next.add(path)
      return next
    })
  }, [])

  return (
    <section className="structure-panel">
      <h2>Структура проекта</h2>

      {!activeId ? (
        <p className="structure-hint">выберите готовый проект в списке выше</p>
      ) : notReady ? (
        <p className="structure-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : (
        <>
          <button type="button" onClick={handleLoad} disabled={loading}>
            {loading ? 'загрузка…' : structure ? 'Обновить структуру' : 'Показать структуру'}
          </button>

          {error && <p className="structure-error">{error}</p>}

          {structure && !error && (
            <>
              <div className="structure-tools">
                <p className="structure-summary">
                  {structure.file_count} файлов · {structure.symbol_count} символов в индексе
                </p>
                <button
                  type="button"
                  className="structure-toggle-all"
                  onClick={() => setExpanded(new Set(allDirPaths(structure.files)))}
                >
                  развернуть папки
                </button>
                <button
                  type="button"
                  className="structure-toggle-all"
                  onClick={() => setExpanded(new Set())}
                >
                  свернуть всё
                </button>
              </div>
              <ul className="structure-tree">
                {buildTree(structure.files).map((node) => (
                  <TreeNodeView
                    key={node.path}
                    node={node}
                    depth={0}
                    expanded={expanded}
                    onToggle={toggle}
                  />
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  )
}

function TreeNodeView({
  node,
  depth,
  expanded,
  onToggle,
}: {
  node: TreeNode
  depth: number
  expanded: Set<string>
  onToggle: (path: string) => void
}) {
  const pad = { paddingLeft: `${0.5 + depth * INDENT_REM}rem` }
  const isOpen = expanded.has(node.path)

  if (node.type === 'dir') {
    return (
      <li className="tree-item">
        <button
          type="button"
          className="tree-row tree-dir"
          style={pad}
          onClick={() => onToggle(node.path)}
          aria-expanded={isOpen}
        >
          <span className="tree-caret">{isOpen ? '▾' : '▸'}</span>
          <span className="tree-icon">📁</span>
          <span className="tree-name">{node.name}</span>
          <span className="tree-count">{node.children.length}</span>
        </button>
        {isOpen && (
          <ul className="tree-children">
            {node.children.map((child) => (
              <TreeNodeView
                key={child.path}
                node={child}
                depth={depth + 1}
                expanded={expanded}
                onToggle={onToggle}
              />
            ))}
          </ul>
        )}
      </li>
    )
  }

  const symbols = node.file.symbols
  const hasSymbols = symbols.length > 0
  const symbolPad = { paddingLeft: `${0.5 + (depth + 1) * INDENT_REM}rem` }

  return (
    <li className="tree-item">
      <button
        type="button"
        className="tree-row tree-file"
        style={pad}
        onClick={() => hasSymbols && onToggle(node.path)}
        aria-expanded={hasSymbols ? isOpen : undefined}
      >
        <span className="tree-caret">{hasSymbols ? (isOpen ? '▾' : '▸') : ''}</span>
        <span className="tree-icon">📄</span>
        <span className="tree-name">{node.name}</span>
        {node.file.lang && <span className="tree-lang">{node.file.lang}</span>}
        {hasSymbols && <span className="tree-count">{symbols.length}</span>}
        {node.file.excluded && (
          <span className="tree-excluded" title="исключён из индекса (находки скана секретов)">
            исключён
          </span>
        )}
      </button>
      {isOpen && hasSymbols && (
        <ul className="tree-symbols">
          {symbols.map((s, i) => (
            <li key={`${s.symbol}-${s.start_line}-${i}`} className="tree-symbol" style={symbolPad}>
              {s.kind && <span className="tree-symbol-kind">{s.kind}</span>}
              <span className="tree-symbol-name">{s.symbol}</span>
              <span className="tree-symbol-lines">
                {s.start_line}–{s.end_line}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export default StructurePanel
