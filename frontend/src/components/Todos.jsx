import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { CheckIcon, PlusIcon, TrashIcon } from './Icons.jsx'

function TodoRow({ todo, onToggle, onEdit, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(todo.text)

  async function commit() {
    setEditing(false)
    const next = text.trim()
    if (!next) { setText(todo.text); return }
    if (next !== todo.text) await onEdit(todo.id, next)
  }

  return (
    <div className="todo-row">
      <button
        className={`todo-check${todo.done ? ' done' : ''}`}
        onClick={() => onToggle(todo.id, !todo.done)}
        aria-label={todo.done ? 'Mark as not done' : 'Mark as done'}
      >
        {todo.done && <CheckIcon width={11} height={11} />}
      </button>

      {editing ? (
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setText(todo.text); setEditing(false) } }}
          style={{ flex: 1, background: 'none', border: 0, borderBottom: '1px solid var(--axis)', fontSize: 14.5, outline: 'none' }}
        />
      ) : (
        <button className={`todo-text${todo.done ? ' done' : ''}`} onClick={() => setEditing(true)}>
          {todo.text}
        </button>
      )}

      <button className="icon-btn" onClick={() => onDelete(todo.id)} aria-label="Delete">
        <TrashIcon width={14} height={14} />
      </button>
    </div>
  )
}

export default function Todos() {
  const [todos, setTodos] = useState(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  async function refresh() {
    setTodos(await api.todos())
  }
  useEffect(() => { refresh() }, [])

  async function submit() {
    const text = draft.trim()
    if (!text || busy) return
    setBusy(true)
    try {
      await api.addTodo(text)
      setDraft('')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  if (!todos) return <div className="empty">Loading…</div>

  const open = todos.filter((t) => !t.done)
  const done = todos.filter((t) => t.done)

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2 className="card-title">To-do</h2>
          <span className="card-sub">{open.length} open{done.length > 0 && ` · ${done.length} done`}</span>
        </div>
        <div className="todo-input-row">
          <input
            placeholder="Add something to do…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
          <button onClick={submit} disabled={!draft.trim() || busy} aria-label="Add">
            <PlusIcon width={16} height={16} />
          </button>
        </div>
      </section>

      {todos.length === 0 ? (
        <section className="card">
          <div className="empty">Nothing on the list. Add the first thing above.</div>
        </section>
      ) : (
        <>
          {open.length > 0 && (
            <section className="card">
              {open.map((t) => (
                <TodoRow
                  key={t.id}
                  todo={t}
                  onToggle={async (id, done) => { await api.updateTodo(id, { done }); refresh() }}
                  onEdit={async (id, text) => { await api.updateTodo(id, { text }); refresh() }}
                  onDelete={async (id) => { await api.deleteTodo(id); refresh() }}
                />
              ))}
            </section>
          )}

          {done.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2 className="card-title">Done</h2>
                <button className="card-sub" style={{ background: 'none', border: 0, color: 'var(--accent)', padding: 0 }}
                  onClick={async () => { await api.clearCompletedTodos(); refresh() }}>
                  Clear completed
                </button>
              </div>
              {done.map((t) => (
                <TodoRow
                  key={t.id}
                  todo={t}
                  onToggle={async (id, done) => { await api.updateTodo(id, { done }); refresh() }}
                  onEdit={async (id, text) => { await api.updateTodo(id, { text }); refresh() }}
                  onDelete={async (id) => { await api.deleteTodo(id); refresh() }}
                />
              ))}
            </section>
          )}
        </>
      )}
    </>
  )
}
