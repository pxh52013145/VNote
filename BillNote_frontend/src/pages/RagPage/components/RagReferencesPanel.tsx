import { type ReactNode, useMemo, useState } from 'react'

import { useRagStore, type RagChatMessage } from '@/store/ragStore'
import { openExternalUrl } from '@/utils'
import { extractQueryKeywords } from '@/utils/ragKeywords'

const extractTimeRange = (text: string) => {
  const m = /TIME=([0-9:]{1,2}:[0-9]{2}(?::[0-9]{2})?-[0-9:]{1,2}:[0-9]{2}(?::[0-9]{2})?)/.exec(text)
  return m?.[1] ?? null
}

const extractSourceUrl = (text: string) => {
  const m = /(?:^\[?SOURCE\]=)(.+)$/im.exec(text)
  return m?.[1]?.trim() ?? null
}

const extractPlatformVideoId = (content: string, documentName: string) => {
  const vid = /VID=([^\]]+)/i.exec(content)?.[1]?.trim()
  const platform = /PLATFORM=([^\]]+)/i.exec(content)?.[1]?.trim()
  if (vid && platform) return { platform, videoId: vid }

  const m = /\[([^:\]]+):([^\]]+)\]\s*$/i.exec(documentName)
  if (m?.[1] && m?.[2]) return { platform: m[1].trim(), videoId: m[2].trim() }

  return null
}

const buildSourceUrlFallback = (platform: string, videoId: string) => {
  const p = String(platform || '').toLowerCase()
  const v = String(videoId || '').trim()
  if (!v) return null

  if (p === 'bilibili') {
    const m = /^(BV[0-9A-Za-z]+)(?:_p([0-9]+))?$/i.exec(v)
    if (!m?.[1]) return `https://www.bilibili.com/video/${v}`
    const base = m[1]
    const part = m[2]
    return part ? `https://www.bilibili.com/video/${base}?p=${part}` : `https://www.bilibili.com/video/${base}`
  }
  return null
}

const parseTimestampSeconds = (value: string) => {
  const parts = String(value || '')
    .trim()
    .split(':')
    .map(v => Number(v))
  if (parts.some(v => Number.isNaN(v))) return null
  if (parts.length === 2) return parts[0] * 60 + parts[1]
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  return null
}

const buildJumpUrl = (sourceUrl: string, timeRange: string | null) => {
  const raw = String(sourceUrl || '').trim()
  if (!/^https?:\/\//i.test(raw)) return null

  try {
    const u = new URL(raw)
    if (timeRange) {
      const start = timeRange.split('-')[0]
      const seconds = parseTimestampSeconds(start)
      if (seconds != null) {
        u.searchParams.set('t', String(seconds))
      }
    }
    return u.toString()
  } catch {
    return raw
  }
}

const stripSegmentTags = (text: string) => {
  return text
    .replace(/^\[?VID=[^\]]+\]\[PLATFORM=[^\]]+\]\[TIME=[^\]]+\]\s*/i, '')
    .replace(/^\[TIME=[^\]]+\]\s*/i, '')
    .trim()
}

const stripHeaderLines = (text: string) => {
  const lines = String(text || '').split('\n')
  const filtered = lines.filter(line => !/^\[?(TITLE|PLATFORM|VIDEO_ID|SOURCE)\]=/i.test(line.trim()))
  return filtered.join('\n').trim()
}

const escapeRegExp = (value: string) => String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const highlightText = (text: string, keywords: string[]): ReactNode => {
  const raw = String(text || '')
  const kws = Array.isArray(keywords) ? keywords.filter(Boolean) : []
  if (!raw || kws.length === 0) return raw

  const uniq: string[] = []
  const seen = new Set<string>()
  for (const kw of kws) {
    const k = String(kw || '').trim()
    if (!k) continue
    const key = k.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    uniq.push(k)
  }
  if (uniq.length === 0) return raw

  // Prefer longer matches to avoid highlighting tiny substrings when a longer keyword exists.
  uniq.sort((a, b) => b.length - a.length)

  const pattern = uniq.map(escapeRegExp).join('|')
  if (!pattern) return raw

  const re = new RegExp(`(${pattern})`, 'gi')
  const parts = raw.split(re)
  const lookup = new Set(uniq.map(k => k.toLowerCase()))

  return parts.map((part, i) => {
    if (!part) return null
    const hit = lookup.has(part.toLowerCase())
    if (!hit) return <span key={i}>{part}</span>
    return (
      <mark key={i} className="px-0.5 rounded bg-yellow-200/70 text-slate-900 font-semibold">
        {part}
      </mark>
    )
  })
}

type RefTab = 'segments' | 'videos'

const RagReferencesPanel = () => {
  const conversations = useRagStore(state => state.conversations)
  const currentConversationId = useRagStore(state => state.currentConversationId)
  const selectedReferenceMessageId = useRagStore(state =>
    state.currentConversationId ? state.selectedReferenceByConversation[state.currentConversationId] || null : null
  )

  const [tab, setTab] = useState<RefTab>('segments')

  const currentConversation = useMemo(() => {
    return conversations.find(c => c.id === currentConversationId) || null
  }, [conversations, currentConversationId])

  const selected = useMemo(() => {
    const messages = currentConversation?.messages || []
    if (messages.length === 0) {
      return { message: null as RagChatMessage | null, resources: [], keywords: [] as string[], query: '' }
    }

    let pickedIndex = -1
    if (selectedReferenceMessageId) {
      pickedIndex = messages.findIndex(m => m && m.id === selectedReferenceMessageId)
    }

    // Default to the latest assistant message (even if it has no citations), to avoid showing stale references.
    if (pickedIndex < 0) {
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i]?.role === 'assistant') {
          pickedIndex = i
          break
        }
      }
    }

    const msg = pickedIndex >= 0 ? (messages[pickedIndex] as RagChatMessage) : null
    const resources = msg?.role === 'assistant' && Array.isArray(msg.resources) ? msg.resources : []

    let query = String(msg?.query || '').trim()
    if (!query && pickedIndex >= 0) {
      for (let i = pickedIndex - 1; i >= 0; i--) {
        const m = messages[i]
        if (m?.role === 'user') {
          query = String(m.content || '').trim()
          break
        }
      }
    }

    const keywords = Array.isArray(msg?.keywords) && msg.keywords.length > 0 ? msg.keywords : extractQueryKeywords(query)
    return { message: msg, resources, keywords, query }
  }, [currentConversation, selectedReferenceMessageId])

  const videos = useMemo(() => {
    type VideoItem = {
      key: string
      datasetName: string
      documentId: string
      documentName: string
      sourceUrl: string | null
      bestScore: number
      hitCount: number
      timeRanges: string[]
    }

    const items = new Map<string, VideoItem>()
    for (const r of selected.resources || []) {
      const docKey = String(r.document_id || r.document_name || r.segment_id || '').trim()
      if (!docKey) continue

      const time = extractTimeRange(r.content)
      const meta = extractPlatformVideoId(r.content, r.document_name)
      const source = extractSourceUrl(r.content) || (meta ? buildSourceUrlFallback(meta.platform, meta.videoId) : null)
      const score = typeof r.score === 'number' ? r.score : Number(r.score || 0)

      const existing = items.get(docKey)
      if (!existing) {
        items.set(docKey, {
          key: docKey,
          datasetName: r.dataset_name,
          documentId: r.document_id,
          documentName: r.document_name,
          sourceUrl: source,
          bestScore: Number.isFinite(score) ? score : 0,
          hitCount: 1,
          timeRanges: time ? [time] : [],
        })
        continue
      }

      existing.hitCount += 1
      if (source && !existing.sourceUrl) existing.sourceUrl = source
      if (Number.isFinite(score) && score > existing.bestScore) existing.bestScore = score
      if (time && !existing.timeRanges.includes(time)) existing.timeRanges.push(time)
    }

    return Array.from(items.values()).sort((a, b) => b.bestScore - a.bestScore)
  }, [selected.resources])

  return (
    <>
      <div className="h-16 px-6 border-b border-slate-100 flex items-center">
        <h2 className="font-semibold text-slate-800">参考资料</h2>
      </div>

      <div className="p-6 flex-1 overflow-y-auto">
        <div className="p-4 bg-amber-50 rounded-lg border border-amber-100 text-sm text-amber-800 mb-4">
          <h4 className="font-semibold mb-1">引用系统已启用</h4>
          <p>回答将引用知识库片段的具体位置，便于核对与复习。</p>
        </div>

        <div className="mb-4 space-y-2">
          <div className="inline-flex items-center gap-1 p-1 rounded-lg bg-slate-50 border border-slate-200 shrink-0">
            <button
              type="button"
              onClick={() => setTab('segments')}
              className={[
                'h-8 px-3 text-xs font-semibold rounded-md transition-colors border whitespace-nowrap leading-none',
                tab === 'segments'
                  ? 'bg-white text-slate-900 shadow-sm border-slate-200'
                  : 'text-slate-500 border-transparent hover:bg-white/60 hover:text-slate-700',
              ].join(' ')}
            >
              引用详情
            </button>
            <button
              type="button"
              onClick={() => setTab('videos')}
              className={[
                'h-8 px-3 text-xs font-semibold rounded-md transition-colors border whitespace-nowrap leading-none',
                tab === 'videos'
                  ? 'bg-white text-slate-900 shadow-sm border-slate-200'
                  : 'text-slate-500 border-transparent hover:bg-white/60 hover:text-slate-700',
              ].join(' ')}
            >
              相关视频
            </button>
          </div>

          <div className="text-[11px] text-slate-400 leading-snug break-words">
            {selected.message?.role === 'assistant' ? (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span>
                  本条引用：{selected.resources.length} 条{tab === 'videos' ? ` · 视频：${videos.length} 个` : ''}
                </span>
                {selected.keywords.length > 0 && (
                  <span title={selected.keywords.join('、')}>关键词：{selected.keywords.join('、')}</span>
                )}
              </div>
            ) : (
              <div>暂无回复可展示引用</div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          {selected.resources.length === 0 ? (
            <div className="p-4 rounded-lg border border-slate-200 bg-white text-sm text-slate-500">
              <div className="font-medium text-slate-700">本条回复暂无引用</div>
              <ul className="list-disc list-inside mt-2 space-y-1 text-slate-600">
                <li>先在左侧点击“生成笔记并入库”，并等待状态变为“已入库”。</li>
                <li>如果你在 Dify 里删过/换过 Dataset，确认 Dify App 也绑定的是同一个 Dataset。</li>
                <li>仍无引用时，检查 Dify App 是否启用 Knowledge 检索并已发布（Publish）。</li>
              </ul>
            </div>
          ) : (
            <>
              {tab === 'videos' ? (
                <>
                  {videos.map(v => (
                    <div key={v.key} className="p-4 bg-white border border-slate-200 rounded-lg shadow-sm">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-semibold text-slate-500">{v.datasetName}</div>
                          <div className="mt-1 text-sm font-medium text-slate-800 truncate">
                            {highlightText(v.documentName, selected.keywords)}
                          </div>
                          <div className="mt-1 text-[11px] text-slate-400">
                            命中片段：{v.hitCount} · best score: {v.bestScore.toFixed(3)}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {v.sourceUrl && (
                            <button
                              type="button"
                              onClick={() => openExternalUrl(v.sourceUrl!)}
                              className="text-xs font-bold text-primary bg-primary-light px-2 py-1 rounded border border-slate-200 whitespace-nowrap hover:opacity-90"
                              title="打开视频"
                            >
                              打开
                            </button>
                          )}
                        </div>
                      </div>
                      {v.timeRanges.length > 0 && (
                        <div className="mt-3 text-xs text-slate-600">
                          <div className="font-semibold text-slate-700 mb-1">命中时间戳</div>
                          <div className="flex flex-wrap gap-2">
                            {v.timeRanges.slice(0, 12).map(t => {
                              const jumpUrl = v.sourceUrl ? buildJumpUrl(v.sourceUrl, t) : null
                              return (
                                <button
                                  key={t}
                                  type="button"
                                  onClick={() => (jumpUrl ? openExternalUrl(jumpUrl) : undefined)}
                                  className={[
                                    'text-[11px] px-2 py-1 rounded border border-slate-200 whitespace-nowrap',
                                    jumpUrl ? 'hover:bg-slate-50 text-slate-700' : 'text-slate-400 cursor-default',
                                  ].join(' ')}
                                  title={jumpUrl ? '打开原片并跳转到该时间点' : undefined}
                                >
                                  {t}
                                </button>
                              )
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </>
              ) : (
                selected.resources.map(r => {
                  const time = extractTimeRange(r.content)
                  const meta = extractPlatformVideoId(r.content, r.document_name)
                  const source =
                    extractSourceUrl(r.content) || (meta ? buildSourceUrlFallback(meta.platform, meta.videoId) : null)
                  const jumpUrl = source ? buildJumpUrl(source, time) : null
                  const snippet = stripHeaderLines(stripSegmentTags(r.content))
                  return (
                    <div
                      key={r.segment_id}
                      className="p-4 bg-white border border-slate-200 rounded-lg shadow-sm hover:shadow-md transition-shadow"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-semibold text-slate-500">{r.dataset_name}</div>
                          <div className="mt-1 text-sm font-medium text-slate-800 truncate">
                            {highlightText(r.document_name, selected.keywords)}
                          </div>
                          <div className="mt-1 text-[11px] text-slate-400">
                            score: {typeof r.score === 'number' ? r.score.toFixed(3) : r.score}
                          </div>
                        </div>
                        {time && (
                          <button
                            type="button"
                            onClick={() => (jumpUrl ? openExternalUrl(jumpUrl) : undefined)}
                            className={[
                              'text-xs font-bold text-primary bg-primary-light px-2 py-1 rounded border border-slate-200 whitespace-nowrap',
                              jumpUrl ? 'cursor-pointer hover:opacity-90' : 'cursor-default',
                            ].join(' ')}
                            title={jumpUrl ? '打开原片并跳转到该时间点' : undefined}
                          >
                            {time}
                          </button>
                        )}
                      </div>
                      <div className="mt-3 text-xs text-slate-600 leading-relaxed whitespace-pre-wrap break-words">
                        {highlightText(snippet, selected.keywords)}
                      </div>
                    </div>
                  )
                })
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}

export default RagReferencesPanel
