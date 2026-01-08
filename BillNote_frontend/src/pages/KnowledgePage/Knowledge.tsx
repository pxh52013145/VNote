import { useEffect, useMemo, useState } from 'react'
import { FileText, Plus, RefreshCcw, Search, SlidersHorizontal } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import MarkdownViewer from '@/pages/HomePage/components/MarkdownViewer'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useTaskStore } from '@/store/taskStore'
import { useSyncStore } from '@/store/syncStore'
import { get_task_status } from '@/services/note'
import type { SyncScanItem } from '@/services/sync'

const formatDateMs = (ms?: number | null) => {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return ''
  try {
    return new Date(ms).toISOString().slice(0, 10)
  } catch {
    return ''
  }
}

const formatIso = (iso?: string | null) => {
  const v = String(iso || '').trim()
  if (!v) return ''
  const d = new Date(v)
  if (isNaN(d.getTime())) return ''
  try {
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).replace(/\//g, '-')
  } catch {
    return ''
  }
}

const getSyncBadge = (status: string) => {
  const s = String(status || '').toUpperCase()
  if (s === 'LOCAL_ONLY') return { text: '本地', className: 'bg-sky-50 text-sky-700 border-sky-200' }
  if (s === 'DIFY_ONLY') return { text: 'DIFY', className: 'bg-violet-50 text-violet-700 border-violet-200' }
  if (s === 'DIFY_ONLY_NO_BUNDLE') return { text: 'DIFY(缺包)', className: 'bg-violet-50 text-violet-700 border-violet-200' }
  if (s === 'SYNCED') return { text: '已同步', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' }
  if (s === 'PARTIAL') return { text: '部分', className: 'bg-amber-50 text-amber-700 border-amber-200' }
  if (s === 'CONFLICT') return { text: '冲突', className: 'bg-rose-50 text-rose-700 border-rose-200' }
  if (s === 'DELETED') return { text: '已删除', className: 'bg-rose-50 text-rose-700 border-rose-200' }
  if (s === 'DIFY_ONLY_LEGACY') return { text: 'DIFY(旧)', className: 'bg-slate-50 text-slate-600 border-slate-200' }
  return { text: s || '未知', className: 'bg-slate-50 text-slate-600 border-slate-200' }
}

type DisplayItem = SyncScanItem & { _key: string }

type FilterState = {
  showLocal: boolean
  showDify: boolean
  showSynced: boolean
  showUnsynced: boolean
  platforms: string[] // empty => all
}

const FILTER_STORAGE_KEY = 'knowledge_filters_v1'
const DEFAULT_FILTERS: FilterState = {
  showLocal: true,
  showDify: false,
  showSynced: true,
  showUnsynced: true,
  platforms: [],
}

const KnowledgePage = () => {
  const navigate = useNavigate()

  const tasks = useTaskStore(state => state.tasks)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)
  const upsertTaskFromBackend = useTaskStore(state => state.upsertTaskFromBackend)
  const removeTask = useTaskStore(state => state.removeTask)

  const currentTask = useMemo(() => tasks.find(t => t.id === currentTaskId) || null, [tasks, currentTaskId])

  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'failed'>('idle')
  const [query, setQuery] = useState('')
  const [remoteSelected, setRemoteSelected] = useState<DisplayItem | null>(null)
  const [actionBusyKey, setActionBusyKey] = useState<string | null>(null)
  const [filterOpen, setFilterOpen] = useState(false)
  const [filters, setFilters] = useState<FilterState>(() => {
    try {
      const raw = localStorage.getItem(FILTER_STORAGE_KEY)
      if (!raw) return DEFAULT_FILTERS
      const parsed = JSON.parse(raw) as Partial<FilterState>
      return {
        showLocal: typeof parsed.showLocal === 'boolean' ? parsed.showLocal : DEFAULT_FILTERS.showLocal,
        showDify: typeof parsed.showDify === 'boolean' ? parsed.showDify : DEFAULT_FILTERS.showDify,
        showSynced: typeof parsed.showSynced === 'boolean' ? parsed.showSynced : DEFAULT_FILTERS.showSynced,
        showUnsynced: typeof parsed.showUnsynced === 'boolean' ? parsed.showUnsynced : DEFAULT_FILTERS.showUnsynced,
        platforms: Array.isArray(parsed.platforms) ? parsed.platforms.map(String) : DEFAULT_FILTERS.platforms,
      }
    } catch {
      return DEFAULT_FILTERS
    }
  })
  const [bootstrapped, setBootstrapped] = useState(false)

  const syncItems = useSyncStore(state => state.items)
  const syncLoading = useSyncStore(state => state.loading)
  const syncProfile = useSyncStore(state => state.profile)
  const syncLastScannedAt = useSyncStore(state => state.lastScannedAt)
  const syncLoadCached = useSyncStore(state => state.loadCached)
  const syncScan = useSyncStore(state => state.scan)
  const syncPush = useSyncStore(state => state.push)
  const syncPull = useSyncStore(state => state.pull)
  const syncDeleteRemote = useSyncStore(state => state.deleteRemote)
  const syncCopyAsNew = useSyncStore(state => state.copyAsNew)

  useEffect(() => {
    if (!currentTask) {
      setStatus('idle')
    } else if (currentTask.status === 'PENDING') {
      setStatus('loading')
    } else if (currentTask.status === 'SUCCESS') {
      setStatus('success')
    } else if (currentTask.status === 'FAILED' || currentTask.status === 'CANCELLED') {
      setStatus('failed')
    }
  }, [currentTask])

  useEffect(() => {
    if (bootstrapped) return
    setBootstrapped(true)
    syncLoadCached({ silent: true })
  }, [bootstrapped, syncLoadCached])

  useEffect(() => {
    try {
      localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters))
    } catch {
      // ignore
    }
  }, [filters])

  useEffect(() => {
    if (currentTaskId) {
      setRemoteSelected(null)
    }
  }, [currentTaskId])

  const displayItems = useMemo<DisplayItem[]>(() => {
    const base: DisplayItem[] = Array.isArray(syncItems)
      ? syncItems.map((i, idx) => ({
          ...i,
          _key: String(i.source_key || i.dify_note_document_id || i.local_task_id || idx),
        }))
      : []

    const knownLocalIds = new Set(base.map(i => String(i.local_task_id || '').trim()).filter(Boolean))
    const fallback: DisplayItem[] = tasks
      .filter(t => t?.id && !knownLocalIds.has(String(t.id)))
      .map(t => ({
        status: 'LOCAL_ONLY',
        title: t.audioMeta?.title || '未命名笔记',
        platform: t.audioMeta?.platform || t.platform || '',
        video_id: t.audioMeta?.video_id || '',
        created_at_ms: Date.parse(t.createdAt || '') || null,
        source_key: null,
        sync_id: null,
        local_task_id: t.id,
        local_has_note: Array.isArray(t.markdown)
          ? t.markdown.some(m => Boolean(String((m as any)?.content ?? m ?? '').trim()))
          : Boolean(String(t.markdown || '').trim()),
        local_has_transcript: Boolean((t.transcript?.segments || []).length) || Boolean(String(t.transcript?.full_text || '').trim()),
        _key: `task:${t.id}`,
      }))

    return [...base, ...fallback]
  }, [syncItems, tasks])

  const platformOptions = useMemo(() => {
    const set = new Set<string>()
    for (const i of displayItems) {
      const p = String(i.platform || '').trim() || 'unknown'
      set.add(p)
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [displayItems])

  const hasActiveFilters = useMemo(() => {
    return (
      filters.showLocal !== DEFAULT_FILTERS.showLocal ||
      filters.showDify !== DEFAULT_FILTERS.showDify ||
      filters.showSynced !== DEFAULT_FILTERS.showSynced ||
      filters.showUnsynced !== DEFAULT_FILTERS.showUnsynced ||
      (filters.platforms?.length || 0) > 0
    )
  }, [filters])

  const filtered = useMemo(() => {
    let list = displayItems

    const q = query.trim().toLowerCase()
    if (q) {
      list = list.filter(i => {
        const title = String(i.title || '').toLowerCase()
        const vid = String(i.video_id || '').toLowerCase()
        return title.includes(q) || vid.includes(q)
      })
    }

    const selectedPlatforms = Array.isArray(filters.platforms) ? filters.platforms : []
    if (selectedPlatforms.length > 0) {
      const set = new Set(selectedPlatforms.map(v => String(v || '').trim()).filter(Boolean))
      list = list.filter(i => set.has(String(i.platform || '').trim() || 'unknown'))
    }

    const wantLocal = Boolean(filters.showLocal)
    const wantDify = Boolean(filters.showDify)
    if (wantLocal || wantDify) {
      list = list.filter(i => {
        const status = String(i.status || '').toUpperCase()
        const hasLocal = Boolean(String(i.local_task_id || '').trim())
        const hasDify =
          status !== 'LOCAL_ONLY' &&
          (status !== '' ||
            Boolean(
              i.dify_note_document_id ||
                i.dify_transcript_document_id ||
                i.remote_has_note ||
                i.remote_has_transcript
            ))
        return (wantLocal && hasLocal) || (wantDify && hasDify)
      })
    }

    if (!(filters.showSynced && filters.showUnsynced)) {
      list = list.filter(i => {
        const status = String(i.status || '').toUpperCase()
        const isSynced = status === 'SYNCED'
        return (filters.showSynced && isSynced) || (filters.showUnsynced && !isSynced)
      })
    }

    return list
  }, [displayItems, query, filters])

  const ensureTaskLoaded = async (taskId: string) => {
    const id = String(taskId || '').trim()
    if (!id) return

    const existing = tasks.find(t => t.id === id) || null
    const hasMarkdown = existing
      ? Array.isArray(existing.markdown)
        ? existing.markdown.length > 0
        : Boolean(String(existing.markdown || '').trim())
      : false
    if (existing && hasMarkdown) return

    const res = await get_task_status(id, { silent: true })
    upsertTaskFromBackend(id, res)
  }

  const handleSelect = async (item: DisplayItem) => {
    const localId = String(item.local_task_id || '').trim()
    if (localId) {
      setRemoteSelected(null)
      await ensureTaskLoaded(localId)
      setCurrentTask(localId)
      return
    }

    setCurrentTask(null)
    setRemoteSelected(item)
  }

  useEffect(() => {
    if (currentTaskId || remoteSelected) return
    const firstLocal = displayItems.find(i => String(i.local_task_id || '').trim())
    const firstId = String(firstLocal?.local_task_id || '').trim()
    if (!firstId) return
    ensureTaskLoaded(firstId)
      .then(() => setCurrentTask(firstId))
      .catch(() => {})
  }, [currentTaskId, remoteSelected, displayItems])

  const resetFilters = () => setFilters({ ...DEFAULT_FILTERS, platforms: [] })

  const togglePlatform = (platform: string, checked: boolean) => {
    const key = String(platform || '').trim() || 'unknown'
    setFilters(prev => {
      const all = (prev.platforms?.length || 0) === 0
      const selected = new Set<string>(all ? platformOptions : (prev.platforms || []).map(String))
      if (checked) selected.add(key)
      else selected.delete(key)
      const next = Array.from(selected).filter(Boolean)
      if (next.length === platformOptions.length) {
        return { ...prev, platforms: [] }
      }
      return { ...prev, platforms: next }
    })
  }

  return (
    <ResizablePanelGroup direction="horizontal" autoSaveId="note-layout" className="h-full w-full bg-slate-50">
      <ResizablePanel defaultSize={25} minSize={16} maxSize={40} className="min-w-[280px]">
        <div className="flex h-full flex-col border-r border-slate-200 bg-white shadow-sm">
          <Dialog open={filterOpen} onOpenChange={setFilterOpen}>
            <DialogContent className="max-w-[520px]">
              <DialogHeader>
                <DialogTitle>筛选</DialogTitle>
              </DialogHeader>

              <div className="space-y-5">
                <div>
                  <div className="text-sm font-medium text-slate-800">来源</div>
                  <div className="mt-2 grid grid-cols-2 gap-3">
                    <label className="flex items-center gap-2 text-sm text-slate-700">
                      <Checkbox checked={filters.showLocal} onCheckedChange={v => setFilters(f => ({ ...f, showLocal: v === true }))} />
                      <span>本地</span>
                    </label>
                    <label className="flex items-center gap-2 text-sm text-slate-700">
                      <Checkbox checked={filters.showDify} onCheckedChange={v => setFilters(f => ({ ...f, showDify: v === true }))} />
                      <span>Dify</span>
                    </label>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">默认只显示“本地知识库”</div>
                </div>

                <div>
                  <div className="text-sm font-medium text-slate-800">同步状态</div>
                  <div className="mt-2 grid grid-cols-2 gap-3">
                    <label className="flex items-center gap-2 text-sm text-slate-700">
                      <Checkbox checked={filters.showSynced} onCheckedChange={v => setFilters(f => ({ ...f, showSynced: v === true }))} />
                      <span>已同步</span>
                    </label>
                    <label className="flex items-center gap-2 text-sm text-slate-700">
                      <Checkbox checked={filters.showUnsynced} onCheckedChange={v => setFilters(f => ({ ...f, showUnsynced: v === true }))} />
                      <span>未同步</span>
                    </label>
                  </div>
                </div>

                <div>
                  <div className="text-sm font-medium text-slate-800">视频平台</div>
                  <div className="mt-2 grid max-h-56 grid-cols-2 gap-2 overflow-auto pr-1">
                    {platformOptions.map(p => {
                      const all = (filters.platforms?.length || 0) === 0
                      const checked = all ? true : (filters.platforms || []).includes(p)
                      return (
                        <label key={p} className="flex items-center gap-2 text-sm text-slate-700">
                          <Checkbox checked={checked} onCheckedChange={v => togglePlatform(p, v === true)} />
                          <span className="truncate">{p}</span>
                        </label>
                      )
                    })}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">不勾选任何平台 = 全部平台</div>
                </div>
              </div>

              <DialogFooter className="flex items-center justify-between gap-2">
                <Button type="button" variant="secondary" onClick={resetFilters}>
                  重置
                </Button>
                <Button type="button" onClick={() => setFilterOpen(false)}>
                  完成
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <div className="h-16 px-6 border-b border-slate-100 flex items-center justify-between">
            <h2 className="font-semibold text-slate-800 flex items-center gap-2">
              <FileText className="w-4 h-4 text-brand-500" />
              知识库
            </h2>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="p-1.5 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"
                title="对账扫描（会访问 Dify / MinIO）"
                onClick={() => syncScan({ silent: false })}
              >
                <RefreshCcw className="w-4 h-4" />
              </button>
              <button
                type="button"
                className="relative p-1.5 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"
                title="筛选"
                onClick={() => setFilterOpen(true)}
              >
                <SlidersHorizontal className="w-4 h-4" />
                {hasActiveFilters ? (
                  <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-brand-600" />
                ) : null}
              </button>
              <button
                type="button"
                className="p-1.5 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"
                title="前往添加视频"
                onClick={() => navigate('/rag')}
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>
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
                {syncProfile ? (
                  <>
                    <div className="mt-2 text-[11px] text-slate-500">当前方案：{syncProfile}</div>
                    <div className="mt-1 text-[11px] text-slate-400">
                      {syncLastScannedAt ? `上次对账：${formatIso(syncLastScannedAt)}` : '尚未对账，点右上角刷新'}
                    </div>
                  </>
                ) : null}
              </div>

              {filtered.length === 0 ? (
                <div className="p-6 text-sm text-slate-500 text-center">暂无记录</div>
              ) : (
                filtered.map(item => {
                  const active =
                    Boolean(item.local_task_id && String(item.local_task_id) === currentTaskId) ||
                    remoteSelected?._key === item._key
                  const title = item.title || '未命名笔记'
                  const tag1 = item.platform || ''
                  const badge = getSyncBadge(item.status)
                  const date = formatDateMs(item.created_at_ms || null)

                  return (
                    <div
                      key={item._key}
                      onClick={() => handleSelect(item)}
                      className={[
                        'group p-3 rounded-lg border cursor-pointer transition-all flex flex-col',
                        active
                          ? 'bg-white border-brand-200 shadow-sm ring-1 ring-brand-100'
                          : 'bg-transparent border-transparent hover:bg-slate-100 hover:border-slate-200',
                      ].join(' ')}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <h4
                          className={[
                            'text-sm font-semibold leading-5 line-clamp-2 min-h-[2.5rem]',
                            active ? 'text-brand-700' : 'text-slate-700',
                          ].join(' ')}
                        >
                          {title}
                        </h4>
                        <Badge variant="outline" className={badge.className}>
                          {badge.text}
                        </Badge>
                      </div>

                      <div className="flex flex-wrap items-center mt-2 gap-2">
                        <div className="flex flex-wrap gap-1">
                          {tag1 ? (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-200 text-slate-600 whitespace-nowrap">
                              {tag1}
                            </span>
                          ) : null}
                        </div>
                        <div className="ml-auto flex flex-wrap items-center gap-2 justify-end">
                          {(() => {
                            const s = String(item.status || '').toUpperCase()
                            const isRestore = item.minio_tombstone_exists === true
                            const canPush =
                              Boolean(item.local_task_id) &&
                              s !== 'DELETED'

                            const canPull =
                              Boolean(item.source_key) &&
                              s !== 'DELETED' &&
                              item.minio_bundle_exists !== false &&
                              s !== 'DIFY_ONLY_NO_BUNDLE' &&
                              (s === 'DIFY_ONLY' ||
                                s === 'CONFLICT' ||
                                (s === 'PARTIAL' &&
                                  ((item.remote_has_note && !item.local_has_note) ||
                                    (item.remote_has_transcript && !item.local_has_transcript))))

                            const canDeleteLocal = Boolean(item.local_task_id)
                            const canDeleteRemote =
                              Boolean(item.source_key) &&
                              s !== 'DELETED' &&
                              Boolean(item.remote_has_note || item.remote_has_transcript)

                            return (
                              <>
                                {canPush ? (
                                  <button
                                    type="button"
                                    className="text-[10px] px-2 py-0.5 rounded bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 whitespace-nowrap"
                                    disabled={actionBusyKey === item._key}
                                    onClick={async e => {
                                      e.stopPropagation()
                                      setActionBusyKey(item._key)
                                      try {
                                        await syncPush(item)
                                      } finally {
                                        setActionBusyKey(null)
                                      }
                                    }}
                                  >
                                    {s === 'CONFLICT'
                                      ? '本地覆盖'
                                      : isRestore || s === 'SYNCED'
                                        ? '重新入库'
                                        : s === 'LOCAL_ONLY'
                                          ? '入库'
                                          : item.minio_bundle_exists === false
                                            ? '补传'
                                            : '入库'}
                                  </button>
                                ) : null}

                                {canPull ? (
                                  <button
                                    type="button"
                                    className="text-[10px] px-2 py-0.5 rounded bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-60 whitespace-nowrap"
                                    disabled={actionBusyKey === item._key}
                                    onClick={async e => {
                                      e.stopPropagation()
                                      setActionBusyKey(item._key)
                                      try {
                                        const newTaskId = await syncPull(item, { overwrite: s === 'CONFLICT' })
                                        if (!newTaskId) return
                                        await ensureTaskLoaded(newTaskId)
                                        setRemoteSelected(null)
                                        setCurrentTask(newTaskId)
                                      } finally {
                                        setActionBusyKey(null)
                                      }
                                    }}
                                  >
                                    {s === 'CONFLICT' ? '云端覆盖' : s === 'PARTIAL' ? '补全' : '获取'}
                                  </button>
                                ) : null}

                                {s === 'CONFLICT' ? (
                                  <button
                                    type="button"
                                    className="text-[10px] px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 disabled:opacity-60 whitespace-nowrap"
                                    disabled={actionBusyKey === item._key}
                                    onClick={async e => {
                                      e.stopPropagation()
                                      const useLocal = confirm('保存“本地版本”为副本？\n确定=保存本地副本；取消=保存云端副本。')
                                      setActionBusyKey(item._key)
                                      try {
                                        const newTaskId = await syncCopyAsNew(item, { fromSide: useLocal ? 'local' : 'remote' })
                                        if (!newTaskId) return
                                        await ensureTaskLoaded(newTaskId)
                                        setRemoteSelected(null)
                                        setCurrentTask(newTaskId)
                                      } finally {
                                        setActionBusyKey(null)
                                      }
                                    }}
                                  >
                                    另存
                                  </button>
                                ) : null}

                                {canDeleteLocal ? (
                                  <button
                                    type="button"
                                    className="text-[10px] px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 disabled:opacity-60 whitespace-nowrap"
                                    disabled={actionBusyKey === item._key}
                                    onClick={async e => {
                                      e.stopPropagation()
                                      if (!confirm('确定删除本地记录吗？')) return
                                      setActionBusyKey(item._key)
                                      try {
                                        await removeTask(String(item.local_task_id))
                                        await syncScan({ silent: true })
                                        if (String(item.local_task_id) === currentTaskId) setCurrentTask(null)
                                      } finally {
                                        setActionBusyKey(null)
                                      }
                                    }}
                                  >
                                    删本地
                                  </button>
                                ) : null}

                                {canDeleteRemote ? (
                                  <button
                                    type="button"
                                    className="text-[10px] px-2 py-0.5 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 disabled:opacity-60 whitespace-nowrap"
                                    disabled={actionBusyKey === item._key}
                                    onClick={async e => {
                                      e.stopPropagation()
                                      if (!confirm('确定删除远端 Dify（并写入 tombstone）吗？')) return
                                      setActionBusyKey(item._key)
                                      try {
                                        await syncDeleteRemote(item)
                                        setRemoteSelected(null)
                                      } finally {
                                        setActionBusyKey(null)
                                      }
                                    }}
                                  >
                                    删远端
                                  </button>
                                ) : null}
                              </>
                            )
                          })()}
                          <span
                            title={date}
                            className="text-[10px] text-slate-400 inline-block max-w-[5.5rem] truncate align-middle"
                          >
                            {date}
                          </span>
                        </div>
                      </div>
                    </div>
                  )
                })
              )}
            </div>
            <div id="transcript-dock" className="empty:hidden absolute inset-0 z-20" />
          </div>
        </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      <ResizablePanel defaultSize={75} minSize={30}>
        <div className="flex h-full flex-col overflow-hidden">
          {remoteSelected ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
              <div className="max-w-md">
                <div className="text-lg font-semibold text-slate-800">{remoteSelected.title || 'DIFY 条目'}</div>
                <div className="mt-2 text-sm text-slate-600">
                  本地尚未获取该条目内容。点击“获取”从 Dify 对应的原文包同步到本地。
                </div>
              </div>
              {remoteSelected.source_key && String(remoteSelected.status || '').toUpperCase() === 'DIFY_ONLY_NO_BUNDLE' ? (
                <div className="text-sm text-slate-500">
                  远端缺少原文包（MinIO 中未找到 bundle），无法获取。请在有本地原文的设备上点击一次“入库/补传”，以补齐原文包。
                </div>
              ) : remoteSelected.source_key && String(remoteSelected.status || '').toUpperCase() === 'DELETED' ? (
                <div className="text-sm text-slate-500">该条目在当前方案已标记删除（tombstone）。如需恢复，请在有本地原文的设备重新入库。</div>
              ) : remoteSelected.source_key ? (
                <button
                  type="button"
                  className="rounded bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
                  disabled={actionBusyKey === remoteSelected._key}
                   onClick={async () => {
                     setActionBusyKey(remoteSelected._key)
                     try {
                       const newTaskId = await syncPull(remoteSelected)
                       if (!newTaskId) return
                       await ensureTaskLoaded(newTaskId)
                       setRemoteSelected(null)
                       setCurrentTask(newTaskId)
                     } finally {
                       setActionBusyKey(null)
                    }
                  }}
                >
                  获取到本地
                </button>
              ) : (
                <div className="text-sm text-slate-500">该条目为旧格式文档，暂不支持自动获取。</div>
              )}
            </div>
          ) : (
            <MarkdownViewer status={status} />
          )}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

export default KnowledgePage
