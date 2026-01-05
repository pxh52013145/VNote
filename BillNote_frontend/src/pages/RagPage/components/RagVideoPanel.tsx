import { useEffect, useState } from 'react'
import { Database, Video } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import NoteForm from '@/pages/HomePage/components/NoteForm'
import NoteHistory from '@/pages/HomePage/components/NoteHistory'
import { useTaskStore } from '@/store/taskStore'
import { cn } from '@/lib/utils'

const RagVideoPanel = () => {
  const navigate = useNavigate()
  const [tab, setTab] = useState<'ingest' | 'tasks'>('ingest')

  const tasks = useTaskStore(state => state.tasks)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)
  const setIngestTask = useTaskStore(state => state.setIngestTask)

  useEffect(() => {
    // Keep the ingest form in a clean "new task" state.
    setIngestTask(null)
  }, [setIngestTask])

  const handlePickTask = (taskId: string) => {
    setCurrentTask(taskId)
    setIngestTask(null)
    navigate('/note')
  }

  return (
    <>
      <div className="flex h-16 items-center justify-between border-b border-slate-100 px-6">
        <h2 className="flex items-center gap-2 font-semibold text-slate-800">
          <Database className="h-4 w-4 text-brand-500" />
          视频库
        </h2>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
          {tasks.length} 项
        </span>
      </div>

      <div className="border-b border-slate-100 bg-slate-50 p-4">
        <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-200/50 p-1">
          <button
            type="button"
            onClick={() => setTab('ingest')}
            className={[
              'flex items-center justify-center gap-1.5 rounded-md py-2 text-xs font-semibold transition-all',
              tab === 'ingest' ? 'bg-white text-brand-700 shadow-sm' : 'text-slate-500 hover:text-slate-700',
            ].join(' ')}
          >
            <Video className="h-3.5 w-3.5" />
            添加视频
          </button>
          <button
            type="button"
            onClick={() => setTab('tasks')}
            className={[
              'rounded-md py-2 text-xs font-semibold transition-all',
              tab === 'tasks' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700',
            ].join(' ')}
          >
            任务列表
          </button>
        </div>
      </div>

      <div className={cn('flex-1 overflow-y-auto')}>
        {tab === 'ingest' && (
          <div className="p-4">
            <NoteForm />
          </div>
        )}
        {tab === 'tasks' && (
          <div className="p-4">
            <NoteHistory onSelect={handlePickTask} selectedId={currentTaskId} />
          </div>
        )}
      </div>
    </>
  )
}

export default RagVideoPanel
