import { useTaskStore } from '@/store/taskStore'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { Badge } from '@/components/ui/badge.tsx'
import { cn } from '@/lib/utils.ts'
import { RotateCcw, Trash } from 'lucide-react'
import { Button } from '@/components/ui/button.tsx'
import PinyinMatch from 'pinyin-match'
import Fuse from 'fuse.js'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip.tsx'
import LazyImage from "@/components/LazyImage.tsx";
import {FC, useState ,useEffect } from 'react'

interface NoteHistoryProps {
  onSelect: (taskId: string) => void
  selectedId: string | null
}

const clamp = (value: number, min = 0, max = 100) => Math.max(min, Math.min(max, value))

const isDifyIndexingCompleted = (payload: any) => {
  const docs = payload?.data
  if (!Array.isArray(docs) || docs.length === 0) return false
  return docs.every(d => typeof d === 'object' && d && d.indexing_status === 'completed')
}

const getDifyIndexingProgress = (payload: any) => {
  const docs = payload?.data
  if (!Array.isArray(docs) || docs.length === 0) return null

  let totalSegments = 0
  let completedSegments = 0
  let totalDocs = 0
  let completedDocs = 0

  for (const doc of docs) {
    if (!doc || typeof doc !== 'object') continue

    const total = (doc as any).total_segments
    const completed = (doc as any).completed_segments
    if (typeof total === 'number' && total > 0) {
      totalSegments += total
      if (typeof completed === 'number' && completed >= 0) {
        completedSegments += Math.min(completed, total)
      }
      continue
    }

    const status = (doc as any).indexing_status
    if (typeof status === 'string') {
      totalDocs += 1
      if (status === 'completed') completedDocs += 1
    }
  }

  if (totalSegments > 0) return clamp(Math.round((completedSegments / totalSegments) * 100))
  if (totalDocs > 0) return clamp(Math.round((completedDocs / totalDocs) * 100))
  return null
}

const getStatusLabel = (status: string) => {
  const map: Record<string, string> = {
    PENDING: '排队中',
    PARSING: '解析链接中',
    DOWNLOADING: '下载中',
    TRANSCRIBING: '转录中',
    SUMMARIZING: '总结中',
    FORMATTING: '格式化中',
    SAVING: '保存中',
    SUCCESS: '已完成',
    FAILED: '失败',
    CANCELLED: '已取消',
  }
  return map[status] || status
}

const getTaskProgress = (task: any) => {
  const p = task?.progress
  if (typeof p === 'number' && Number.isFinite(p)) return clamp(Math.round(p))

  const map: Record<string, number> = {
    PENDING: 0,
    PARSING: 5,
    DOWNLOADING: 20,
    TRANSCRIBING: 55,
    SUMMARIZING: 85,
    FORMATTING: 92,
    SAVING: 97,
    SUCCESS: 100,
    FAILED: 0,
    CANCELLED: 0,
  }
  return clamp(map[task?.status] ?? 0)
}

const getDifyTag = (task: any) => {
  if (task?.dify_error) {
    return { text: '入库失败', className: 'bg-rose-50 text-rose-700 border-rose-200' }
  }
  if (task?.status === 'SUCCESS' && task?.dify && !task?.dify?.batch) {
    // Grace period: note may be done while Dify upload is still in-flight.
    return { text: '上传中', className: 'bg-sky-50 text-sky-700 border-sky-200' }
  }
  if (task?.status === 'SUCCESS' && !task?.dify) {
    return { text: '未入库', className: 'bg-slate-50 text-slate-600 border-slate-200' }
  }
  if (!task?.dify?.batch) return null

  const completed = isDifyIndexingCompleted(task?.dify_indexing)
  return completed
    ? { text: '已入库', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' }
    : { text: '入库中', className: 'bg-amber-50 text-amber-700 border-amber-200' }
}

const NoteHistory: FC<NoteHistoryProps> = ({ onSelect, selectedId }) => {
  const tasks = useTaskStore(state => state.tasks)
  const removeTask = useTaskStore(state => state.removeTask)
  const reingestTask = useTaskStore(state => state.reingestTask)
  // 确保baseURL没有尾部斜杠
  const baseURL = String(import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
  const [rawSearch, setRawSearch] = useState('')
  const [search, setSearch] = useState('')
  const [reingestingId, setReingestingId] = useState<string | null>(null)
  const fuse = new Fuse(tasks, {
    keys: ['audioMeta.title'],
    threshold: 0.4 // 匹配精度（越低越严格）
  })
  useEffect(() => {
    const timer = setTimeout(() => {
      if (rawSearch === '') return
      setSearch(rawSearch)
    }, 300) // 300ms 防抖

    return () => clearTimeout(timer)
  }, [rawSearch])
  const filteredTasks = search.trim()
      ? fuse.search(search).map(result => result.item)
      : tasks
  if (filteredTasks.length === 0) {
    return (
        <>
          <div className="mb-2">
            <input
                type="text"
                placeholder="搜索笔记标题..."
                className="w-full rounded border border-neutral-300 px-3 py-1 text-sm outline-none focus:border-primary"
                value={search}
                onChange={e => setSearch(e.target.value)}
            />
          </div>
          <div className="rounded-md border border-neutral-200 bg-neutral-50 py-6 text-center">
            <p className="text-sm text-neutral-500">暂无记录</p>
          </div>
        </>

    )
  }


  return (
    <>
      <div className="mb-2">
        <input
            type="text"
            placeholder="搜索笔记标题..."
            className="w-full rounded border border-neutral-300 px-3 py-1 text-sm outline-none focus:border-primary"
            value={search}
            onChange={e => setSearch(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-2 overflow-hidden">
        {filteredTasks.map(task => {
          const difyTag = getDifyTag(task)
          const isGenerating = task.status !== 'SUCCESS' && task.status !== 'FAILED' && task.status !== 'CANCELLED'
          const isUploading = task.status === 'SUCCESS' && !task.dify_error && !!task?.dify && !task?.dify?.batch
          const isIndexing =
            task.status === 'SUCCESS' &&
            !task.dify_error &&
            !!task?.dify?.batch &&
            !isDifyIndexingCompleted(task?.dify_indexing)
          const hasMarkdown = Array.isArray(task.markdown)
            ? task.markdown.length > 0
            : Boolean(String(task.markdown || '').trim())
          const generationFinished =
            task.status === 'SUCCESS' || task.status === 'FAILED' || task.status === 'CANCELLED'
          const canReingest = generationFinished && hasMarkdown && !isDifyIndexingCompleted(task?.dify_indexing)
          const generationProgress = getTaskProgress(task)
          const indexingProgress = getDifyIndexingProgress(task?.dify_indexing)
          return (
            <div
              key={task.id}
              onClick={() => onSelect(task.id)}
              className={cn(
                'flex cursor-pointer flex-col rounded-md border border-neutral-200 p-3',
                selectedId === task.id && 'border-primary bg-primary-light'
              )}
            >
            <div
              className={cn('flex items-center gap-4')}
            >
              {/* 封面图 */}
              {task.platform === 'local' ? (
                <img
                  src={
                    task.audioMeta.cover_url ? `${task.audioMeta.cover_url}` : '/placeholder.png'
                  }
                  alt="封面"
                  className="h-10 w-12 rounded-md object-cover"
                />
              ) : (
                  <LazyImage

                      src={
                        task.audioMeta.cover_url
                            ? `${baseURL}/image_proxy?url=${encodeURIComponent(task.audioMeta.cover_url)}`
                            : '/placeholder.png'
                      }
                      alt="封面"
                  />
              )}

              {/* 标题 + 状态 */}

              <div className="flex w-full items-center justify-between gap-2">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="line-clamp-2 max-w-[180px] flex-1 overflow-hidden text-sm text-ellipsis">
                        {task.audioMeta.title || '未命名笔记'}
                      </div>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{task.audioMeta.title || '未命名笔记'}</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </div>
            {(isGenerating || isUploading || isIndexing) && (
              <div className="mt-2">
                <div className="flex items-center justify-between text-[10px] text-neutral-600 dark:text-neutral-300">
                  <div className="line-clamp-1">
                    {isGenerating && getStatusLabel(task.status)}
                    {isUploading && '上传到知识库中'}
                    {isIndexing && '入库中'}
                  </div>
                  <div className="tabular-nums">
                    {isGenerating && `${generationProgress}%`}
                    {isIndexing && (typeof indexingProgress === 'number' ? `${indexingProgress}%` : '…')}
                    {isUploading && '…'}
                  </div>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-neutral-200 dark:bg-neutral-800">
                  {isGenerating ? (
                    <div
                      className="h-full rounded bg-primary transition-[width] duration-300"
                      style={{ width: `${generationProgress}%` }}
                    />
                  ) : (
                    <div
                      className={cn(
                        'h-full rounded bg-primary/80',
                        typeof indexingProgress === 'number' && isIndexing
                          ? 'transition-[width] duration-300'
                          : 'w-1/3 animate-pulse'
                      )}
                      style={
                        typeof indexingProgress === 'number' && isIndexing
                          ? { width: `${indexingProgress}%` }
                          : undefined
                      }
                    />
                  )}
                </div>
              </div>
            )}
            <div className={'mt-2 flex items-center justify-between text-[10px]'}>
              <div className="shrink-0 flex items-center gap-2">
                {task.status === 'SUCCESS' && (
                  <div className={'bg-primary w-10 rounded p-0.5 text-center text-white'}>
                    已完成
                  </div>
                )}
                {task.status !== 'SUCCESS' && task.status !== 'FAILED' && task.status !== 'CANCELLED' ? (
                  <div className={'w-10 rounded bg-green-500 p-0.5 text-center text-white'}>
                    等待中
                  </div>
                ) : (
                  <></>
                )}
                {task.status === 'FAILED' && (
                  <div className={'w-10 rounded bg-red-500 p-0.5 text-center text-white'}>失败</div>
                )}
                {task.status === 'CANCELLED' && (
                  <div className={'w-10 rounded bg-neutral-400 p-0.5 text-center text-white'}>已取消</div>
                )}
                {difyTag && (
                  <div className={cn('rounded border px-2 py-0.5 text-center', difyTag.className)}>
                    {difyTag.text}
                  </div>
                )}
              </div>

              <div>
                <TooltipProvider>
                  {canReingest && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          size="small"
                          variant="ghost"
                          disabled={reingestingId === task.id}
                          onClick={async e => {
                            e.stopPropagation()
                            if (reingestingId) return
                            try {
                              setReingestingId(task.id)
                              await reingestTask(task.id)
                            } finally {
                              setReingestingId(null)
                            }
                          }}
                          className="shrink-0"
                        >
                          <RotateCcw className="text-muted-foreground h-4 w-4" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>重新入库</p>
                      </TooltipContent>
                    </Tooltip>
                  )}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        size="small"
                        variant="ghost"
                        onClick={e => {
                          e.stopPropagation()
                          removeTask(task.id)
                        }}
                        className="shrink-0"
                      >
                        <Trash className="text-muted-foreground h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>删除</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              {/*<div className="shrink-0">*/}
              {/*  {task.status === 'SUCCESS' && <Badge variant="default">已完成</Badge>}*/}
              {/*  {task.status !== 'SUCCESS' && task.status === 'FAILED' && (*/}
              {/*    <Badge variant="outline">等待中</Badge>*/}
              {/*  )}*/}
              {/*  {task.status === 'FAILED' && <Badge variant="destructive">失败</Badge>}*/}
              {/*</div>*/}
            </div>
          </div>
          )
        })}
      </div>
    </>
  )
}

export default NoteHistory
