import { useEffect, useMemo, useState } from 'react'
import { FileText, Plus, Search } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import MarkdownViewer from '@/pages/HomePage/components/MarkdownViewer'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { useTaskStore } from '@/store/taskStore'

const formatDate = (iso: string) => {
  try {
    return new Date(iso).toISOString().slice(0, 10)
  } catch {
    return iso
  }
}

const KnowledgePage = () => {
  const navigate = useNavigate()
  const tasks = useTaskStore(state => state.tasks)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)

  const currentTask = useMemo(() => tasks.find(t => t.id === currentTaskId) || null, [tasks, currentTaskId])

  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'failed'>('idle')
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!currentTask) {
      setStatus('idle')
    } else if (currentTask.status === 'PENDING') {
      setStatus('loading')
    } else if (currentTask.status === 'SUCCESS') {
      setStatus('success')
    } else if (currentTask.status === 'FAILED') {
      setStatus('failed')
    }
  }, [currentTask])

  useEffect(() => {
    if (!currentTaskId && tasks.length > 0) {
      setCurrentTask(tasks[0].id)
    }
  }, [currentTaskId, tasks, setCurrentTask])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return tasks
    return tasks.filter(t => (t.audioMeta?.title || '').toLowerCase().includes(q))
  }, [tasks, query])

  return (
    <ResizablePanelGroup direction="horizontal" autoSaveId="note-layout" className="h-full w-full bg-slate-50">
      <ResizablePanel defaultSize={25} minSize={16} maxSize={40}>
        <div className="flex h-full flex-col border-r border-slate-200 bg-white shadow-sm">
        <div className="h-16 px-6 border-b border-slate-100 flex items-center justify-between">
          <h2 className="font-semibold text-slate-800 flex items-center gap-2">
            <FileText className="w-4 h-4 text-brand-500" />
            知识库
          </h2>
          <button
            type="button"
            className="p-1.5 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"
            title="前往添加视频"
            onClick={() => {
              navigate('/rag')
            }}
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        <div className="relative flex-1 overflow-y-auto">
          <div className="p-4 space-y-2">
            <div className="pb-2">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
                <input
                  type="text"
                  placeholder="搜索笔记..."
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  className="w-full pl-9 pr-4 py-2 bg-slate-100 border-none rounded-lg text-sm focus:ring-2 focus:ring-brand-500/50 outline-none placeholder:text-slate-400"
                />
              </div>
            </div>

            {filtered.length === 0 ? (
              <div className="p-6 text-sm text-slate-500 text-center">暂无记录。</div>
            ) : (
              filtered.map(task => {
                const active = task.id === currentTaskId
                const title = task.audioMeta?.title || '未命名笔记'
                const tag1 = task.platform || task.formData?.platform || ''
                const tag2 = task.formData?.style || ''
                return (
                  <div
                    key={task.id}
                    onClick={() => setCurrentTask(task.id)}
                    className={[
                      'group p-3 rounded-lg border cursor-pointer transition-all',
                      active ? 'bg-white border-brand-200 shadow-sm ring-1 ring-brand-100' : 'bg-transparent border-transparent hover:bg-slate-100 hover:border-slate-200',
                    ].join(' ')}
                  >
                    <h4 className={['text-sm font-semibold mb-1', active ? 'text-brand-700' : 'text-slate-700'].join(' ')}>
                      {title}
                    </h4>
                    <div className="flex items-center justify-between mt-2">
                      <div className="flex gap-1">
                        {tag1 ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-200 text-slate-600">
                            {tag1}
                          </span>
                        ) : null}
                        {tag2 ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-200 text-slate-600">
                            {tag2}
                          </span>
                        ) : null}
                      </div>
                      <span className="text-[10px] text-slate-400">{formatDate(task.createdAt)}</span>
                    </div>
                  </div>
                )
              })
            )}
          </div>
          <div
            id="transcript-dock"
            className="empty:hidden absolute inset-0 z-20"
          />
        </div>
        </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      <ResizablePanel defaultSize={75} minSize={30}>
        <div className="flex h-full flex-col overflow-hidden">
          <MarkdownViewer status={status} />
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

export default KnowledgePage
