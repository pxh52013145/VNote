import { useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { v4 as uuidv4 } from 'uuid'
import { ChevronDown, Clock, Loader2, MessageSquare, Plus, Send, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { ragChat, type RagRetrieverResource } from '@/services/rag'
import { useRagStore, type RagChatMessage } from '@/store/ragStore'
import { useTaskStore } from '@/store/taskStore'
import RagConversationList from '@/pages/RagPage/components/RagConversationList'
import { extractQueryKeywords } from '@/utils/ragKeywords'
import { openExternalUrl } from '@/utils'

const isDifyIndexingCompleted = (payload: any) => {
  const docs = payload?.data
  if (!Array.isArray(docs) || docs.length === 0) return false
  return docs.every(d => typeof d === 'object' && d && d.indexing_status === 'completed')
}

const TIME_RANGE_RE =
  /TIME=([0-9:]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:\s*-\s*[0-9:]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)/i

const ORIGIN_TIME_LINK_RE = /\[\s*原片\s*@\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)\s*\]\((https?:\/\/[^)\s]+)\)/gi

const normalizeTimeRange = (value: string | null) => {
  const raw = String(value || '').trim()
  if (!raw) return null
  const parts = raw
    .split('-')
    .map(p => p.trim())
    .filter(Boolean)
  if (parts.length === 0) return null
  if (parts.length === 1) return parts[0]
  return `${parts[0]}-${parts[1]}`
}

const extractTimeRanges = (text: string) => {
  const raw = String(text || '')
  const found: string[] = []

  for (const m of raw.matchAll(
    /TIME=([0-9:]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:\s*-\s*[0-9:]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)/gi
  )) {
    const t = normalizeTimeRange(String(m[1] || ''))
    if (t && !found.includes(t)) found.push(t)
  }

  for (const m of raw.matchAll(/\[\s*原片\s*@\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)\s*\]\((https?:\/\/[^)\s]+)\)/gi)) {
    const t = normalizeTimeRange(String(m[1] || ''))
    if (t && !found.includes(t)) found.push(t)
  }

  for (const m of raw.matchAll(/content-(\d{2})(\d{2})(?!\d)/gi)) {
    const mm = String(m[1] || '').padStart(2, '0')
    const ss = String(m[2] || '').padStart(2, '0')
    const t = normalizeTimeRange(`${mm}:${ss}`)
    if (t && !found.includes(t)) found.push(t)
  }

  return found
}

const extractOriginTimeLinks = (text: string) => {
  const raw = String(text || '')
  const found: Array<{ time: string; url: string }> = []
  for (const m of raw.matchAll(ORIGIN_TIME_LINK_RE)) {
    const time = normalizeTimeRange(String(m[1] || ''))
    const url = String(m[2] || '').trim()
    if (!time || !url) continue
    found.push({ time, url })
  }
  return found
}

const extractSourceUrl = (text: string) => {
  const m = /(?:^\[?SOURCE\]=)(.+)$/im.exec(text)
  return m?.[1]?.trim() ?? null
}

const extractPlatformVideoId = (content: string, documentName: string) => {
  const vid = /VID=([^\]]+)/i.exec(content)?.[1]?.trim()
  const platform = /PLATFORM=([^\]]+)/i.exec(content)?.[1]?.trim()
  if (vid && platform) return { platform, videoId: vid }

  const rawName = String(documentName || '')
  const right = rawName.lastIndexOf(']')
  const left = right > 0 ? rawName.lastIndexOf('[', right) : -1
  if (left >= 0 && right > left) {
    const tag = rawName.slice(left + 1, right).trim()
    const parts = tag
      .split(':')
      .map(p => p.trim())
      .filter(Boolean)
    // Supports "<title> [platform:video_id]" or "<title> [platform:video_id:created_at_ms]".
    if (parts.length >= 2) return { platform: parts[0], videoId: parts[1] }
  }

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

  if (p === 'youtube' || p === 'yt') {
    if (/^https?:\/\//i.test(v)) return v
    return `https://www.youtube.com/watch?v=${encodeURIComponent(v)}`
  }

  if (p === 'douyin') {
    if (/^https?:\/\//i.test(v)) return v
    return `https://www.douyin.com/video/${encodeURIComponent(v)}`
  }

  if (p === 'kuaishou') {
    if (/^https?:\/\//i.test(v)) return v
    return `https://www.kuaishou.com/short-video/${encodeURIComponent(v)}`
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
      const start = timeRange.split('-')[0].trim()
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

const buildTimeJumpIndex = (resources?: RagRetrieverResource[]) => {
  const index = new Map<string, { jumpUrl: string; score: number }>()

  const setIndex = (key: string, jumpUrl: string, score: number) => {
    const k = String(key || '').trim()
    const url = String(jumpUrl || '').trim()
    if (!k || !url) return
    const s = Number.isFinite(score) ? score : 0
    const prev = index.get(k)
    if (!prev || s > prev.score) index.set(k, { jumpUrl: url, score: s })
  }

  for (const r of resources || []) {
    const content = String(r.content || '')
    const score = typeof r.score === 'number' ? r.score : Number(r.score || 0)

    const meta = extractPlatformVideoId(r.content || '', r.document_name || '')
    const source =
      extractSourceUrl(r.content || '') || (meta ? buildSourceUrlFallback(meta.platform, meta.videoId) : null)

    for (const link of extractOriginTimeLinks(content)) {
      setIndex(link.time, link.url, score)
    }

    if (!source) continue

    for (const t of extractTimeRanges(content)) {
      const jumpUrl = buildJumpUrl(source, t)
      if (!jumpUrl) continue
      setIndex(t, jumpUrl, score)
      const start = normalizeTimeRange(t.split('-')[0]) || ''
      if (start) setIndex(start, jumpUrl, score)
    }
  }

  return index
}

const buildBestSourceUrl = (resources?: RagRetrieverResource[]) => {
  for (const r of resources || []) {
    const content = String(r.content || '')
    const direct = extractSourceUrl(content)
    if (direct && /^https?:\/\//i.test(direct)) return direct

    const meta = extractPlatformVideoId(content, String(r.document_name || ''))
    const fallback = meta ? buildSourceUrlFallback(meta.platform, meta.videoId) : null
    if (fallback && /^https?:\/\//i.test(fallback)) return fallback

    const links = extractOriginTimeLinks(content)
    if (links.length > 0 && /^https?:\/\//i.test(links[0].url)) return links[0].url
  }
  return null
}

const injectTimeLinks = (markdown: string) => {
  const raw = String(markdown || '')
  return raw.replace(/\[\s*TIME=([^\]]+)\s*]/gi, (full, rangeRaw, offset, str) => {
    if (typeof offset === 'number' && typeof str === 'string') {
      // Avoid double-wrapping when it's already a Markdown link label: `[TIME=...](...)`.
      if (str[offset + full.length] === '(') return full
    }
    const timeRange = normalizeTimeRange(String(rangeRaw || ''))
    if (!timeRange || !TIME_RANGE_RE.test(`TIME=${timeRange}`)) return full
    return `[${timeRange}](#time=${encodeURIComponent(timeRange)})`
  })
}

const RagChatPanel = () => {
  const userId = useRagStore(state => state.userId)
  const conversations = useRagStore(state => state.conversations)
  const currentConversationId = useRagStore(state => state.currentConversationId)
  const initialized = useRagStore(state => state.initialized)
  const bootstrap = useRagStore(state => state.bootstrap)
  const createConversation = useRagStore(state => state.createConversation)
  const appendMessage = useRagStore(state => state.appendMessage)
  const updateConversation = useRagStore(state => state.updateConversation)
  const setSelectedReferenceMessage = useRagStore(state => state.setSelectedReferenceMessage)
  const selectedReferenceMessageId = useRagStore(state =>
    state.currentConversationId ? state.selectedReferenceByConversation[state.currentConversationId] || null : null
  )

  const tasks = useTaskStore(state => state.tasks)

  const indexedCount = useMemo(() => {
    return tasks.filter(t => t.dify?.batch && isDifyIndexingCompleted(t.dify_indexing)).length
  }, [tasks])

  const currentConversation = useMemo(() => {
    return conversations.find(c => c.id === currentConversationId) || null
  }, [conversations, currentConversationId])

  const messages = currentConversation?.messages || []
  const effectiveSelectedReferenceMessageId = useMemo(() => {
    if (!currentConversationId) return null
    if (selectedReferenceMessageId) return selectedReferenceMessageId
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === 'assistant') return messages[i]?.id || null
    }
    return null
  }, [currentConversationId, messages, selectedReferenceMessageId])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  useEffect(() => {
    const handler = () => {
      void bootstrap()
      toast.success('RAG 配置已切换')
    }
    window.addEventListener('rag-context-changed', handler as any)
    return () => {
      window.removeEventListener('rag-context-changed', handler as any)
    }
  }, [bootstrap])

  useEffect(() => {
    if (!initialized) return
    if (!currentConversationId && conversations.length === 0) {
      createConversation()
    }
  }, [initialized, currentConversationId, conversations.length, createConversation])

  const bottomRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length])

  const canSend = useMemo(() => input.trim().length > 0 && !sending && !!userId, [input, sending, userId])

  const resetConversation = () => {
    createConversation()
    toast.success('已开启新对话')
  }

  const send = async () => {
    const query = input.trim()
    if (!query) return

    setInput('')
    setSending(true)

    const convId = currentConversationId || createConversation()
    const difyConversationId = currentConversation?.difyConversationId

    const userMsg: RagChatMessage = {
      id: uuidv4(),
      role: 'user',
      content: query,
      createdAt: new Date().toISOString(),
    }
    appendMessage(convId, userMsg)

    try {
      const resp = await ragChat({
        query,
        conversation_id: difyConversationId,
        user: userId,
      })

      if (resp.conversation_id) {
        updateConversation(convId, { difyConversationId: resp.conversation_id })
      }

      const keywords = extractQueryKeywords(query)
      const assistantMsg: RagChatMessage = {
        id: resp.message_id || uuidv4(),
        role: 'assistant',
        content: resp.answer || '',
        createdAt: new Date().toISOString(),
        query,
        keywords,
        replyTo: userMsg.id,
        resources: resp.retriever_resources || [],
      }
      appendMessage(convId, assistantMsg)
      setSelectedReferenceMessage(convId, assistantMsg.id)
    } catch (e) {
      console.error(e)
      // `request` already shows an error toast (network/backend). Avoid double-toasting here.
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col h-full relative">
      <div className="h-16 border-b border-slate-200 bg-white/80 backdrop-blur-sm flex items-center px-6 justify-between sticky top-0 z-10">
        <button
          type="button"
          onClick={() => setIsHistoryOpen(true)}
          className="flex items-center gap-3 group hover:bg-slate-50 p-2 -ml-2 rounded-xl transition-all"
        >
          <div className="w-8 h-8 rounded-full bg-primary-light flex items-center justify-center text-primary group-hover:bg-primary group-hover:text-primary-foreground transition-colors">
            <MessageSquare className="w-4 h-4" />
          </div>
          <div className="text-left">
            <h2 className="font-semibold text-slate-800 flex items-center gap-1.5 group-hover:text-primary transition-colors">
              RAG 对话
              <ChevronDown className="w-3.5 h-3.5 text-slate-400 group-hover:text-primary mt-0.5" />
            </h2>
            <p className="text-xs text-slate-500">
              {indexedCount > 0 ? `上下文：已入库 ${indexedCount} 个视频` : '上下文：请先生成笔记并入库'}
            </p>
          </div>
        </button>

        <button
          type="button"
          onClick={resetConversation}
          className="text-sm text-slate-500 hover:text-primary flex items-center gap-1 bg-white border border-slate-200 px-3 py-1.5 rounded-full hover:shadow-sm transition-all"
          disabled={sending}
        >
          <Plus className="w-4 h-4" />
          新对话
        </button>
      </div>

      {isHistoryOpen && (
        <div
          className="absolute inset-0 z-50 bg-slate-900/10 backdrop-blur-[2px] flex items-start justify-center pt-20"
          onClick={() => setIsHistoryOpen(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-[420px] max-h-[620px] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200"
            onClick={e => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50">
              <h3 className="font-semibold text-slate-800 flex items-center gap-2">
                <Clock className="w-4 h-4 text-slate-500" />
                对话历史
              </h3>
              <button
                type="button"
                onClick={() => setIsHistoryOpen(false)}
                className="p-1 hover:bg-slate-200 rounded-full transition-colors"
              >
                <X className="w-4 h-4 text-slate-500" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              <RagConversationList onPicked={() => setIsHistoryOpen(false)} />
            </div>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6 space-y-8">
        {messages.length === 0 ? (
          <div className="flex gap-4 max-w-3xl mx-auto">
            <div className="w-8 h-8 rounded-full bg-primary flex-shrink-0 flex items-center justify-center text-primary-foreground text-xs font-bold">
              AI
            </div>
            <div className="space-y-2">
              <div className="bg-white p-4 rounded-2xl rounded-tl-none border border-slate-200 shadow-sm text-slate-700 text-sm leading-relaxed">
                {indexedCount > 0 ? (
                  <>
                    <p>你好！我可以基于你的“视频笔记 + 转写原文”做检索问答。</p>
                    <ul className="list-disc list-inside mt-2 space-y-1 text-slate-600">
                      <li>“帮我定位：哪里讲到了「某个关键词」？给出时间戳。”</li>
                      <li>“总结这段视频的关键知识点，并引用原文片段。”</li>
                      <li>“「某个概念」和哪些片段相关？给出关联处（带时间戳）。”</li>
                    </ul>
                  </>
                ) : (
                  <>
                    <p>当前还没有可检索的入库内容。</p>
                    <p className="mt-2 text-slate-600">
                      先在左侧添加视频并点击“生成笔记”（会自动写入 Dify 知识库），等待状态显示“已入库”后再来提问。
                    </p>
                    <p className="mt-3 text-slate-600">入库后你可以问：</p>
                    <ul className="list-disc list-inside mt-2 space-y-1 text-slate-600">
                      <li>“帮我定位：哪里讲到了「某个关键词」？给出时间戳。”</li>
                      <li>“把这段视频的知识点按层次列出来，并引用原文。”</li>
                      <li>“这段内容和哪些片段相关？给出关联处（带时间戳）。”</li>
                    </ul>
                  </>
                )}
              </div>
            </div>
          </div>
        ) : (
          messages.map(m => {
            if (m.role === 'user') {
              return (
                <div key={m.id} className="flex gap-4 max-w-3xl mx-auto flex-row-reverse">
                  <div className="w-8 h-8 rounded-full bg-slate-200 flex-shrink-0 flex items-center justify-center text-slate-600 text-xs font-bold">
                    我
                  </div>
                  <div className="space-y-2">
                    <div className="bg-primary p-4 rounded-2xl rounded-tr-none shadow-md text-primary-foreground text-sm leading-relaxed whitespace-pre-wrap">
                      {m.content}
                    </div>
                  </div>
                </div>
              )
            }

            const isSelected = !!(currentConversationId && effectiveSelectedReferenceMessageId === m.id)
            const timeJumpIndex = buildTimeJumpIndex(m.resources)
            const fallbackSourceUrl = buildBestSourceUrl(m.resources)
            const renderedMarkdown = injectTimeLinks(m.content || '')

            return (
              <div key={m.id} className="flex gap-4 max-w-3xl mx-auto">
                <div className="w-8 h-8 rounded-full bg-primary flex-shrink-0 flex items-center justify-center text-primary-foreground text-xs font-bold">
                  AI
                </div>
                <div className="space-y-2 w-full">
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() =>
                      currentConversationId ? setSelectedReferenceMessage(currentConversationId, m.id) : undefined
                    }
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        if (!currentConversationId) return
                        setSelectedReferenceMessage(currentConversationId, m.id)
                      }
                    }}
                    className={[
                      'bg-white p-4 rounded-2xl rounded-tl-none border shadow-sm text-slate-700 text-sm leading-relaxed cursor-pointer',
                      isSelected ? 'border-primary/40 ring-2 ring-primary/10' : 'border-slate-200',
                    ].join(' ')}
                    title="点击查看该条回复的引用"
                  >
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        p: ({ children, ...props }) => (
                          <p className="whitespace-pre-wrap [&:not(:first-child)]:mt-3" {...props}>
                            {children}
                          </p>
                        ),
                        ul: ({ children, ...props }) => (
                          <ul className="my-2 list-disc space-y-1 pl-5" {...props}>
                            {children}
                          </ul>
                        ),
                        ol: ({ children, ...props }) => (
                          <ol className="my-2 list-decimal space-y-1 pl-5" {...props}>
                            {children}
                          </ol>
                        ),
                        li: ({ children, ...props }) => (
                          <li className="whitespace-pre-wrap" {...props}>
                            {children}
                          </li>
                        ),
                        a: ({ href, children, ...props }) => {
                          const rawHref = String(href || '').trim()
                          if (rawHref.toLowerCase().startsWith('#time=')) {
                            let timeRange = rawHref.slice('#time='.length)
                            try {
                              timeRange = decodeURIComponent(timeRange)
                            } catch {
                              // ignore
                            }

                            const normalized = normalizeTimeRange(timeRange)
                            const start = normalized ? normalized.split('-')[0].trim() : ''
                            const jumpUrl =
                              (normalized ? timeJumpIndex.get(normalized)?.jumpUrl : null) ??
                              (start ? timeJumpIndex.get(start)?.jumpUrl : null) ??
                              (fallbackSourceUrl && normalized ? buildJumpUrl(fallbackSourceUrl, normalized) : null)

                            return (
                              <button
                                type="button"
                                onClick={e => {
                                  e.stopPropagation()
                                  if (!jumpUrl) return
                                  void openExternalUrl(jumpUrl)
                                }}
                                className={[
                                  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold transition-colors',
                                  jumpUrl
                                    ? 'border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100'
                                    : 'cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400',
                                ].join(' ')}
                                title={jumpUrl ? '打开原视频并跳转到该时间点' : '未找到该时间段的原视频链接'}
                                disabled={!jumpUrl}
                              >
                                <Clock className="h-3.5 w-3.5" />
                                {normalized || children}
                              </button>
                            )
                          }

                          const hrefTime = extractTimeRanges(rawHref)?.[0] ?? null
                          if (hrefTime) {
                            const normalized = normalizeTimeRange(hrefTime)
                            const jumpUrl =
                              (normalized ? timeJumpIndex.get(normalized)?.jumpUrl : null) ??
                              (fallbackSourceUrl && normalized ? buildJumpUrl(fallbackSourceUrl, normalized) : null)

                            return (
                              <button
                                type="button"
                                onClick={e => {
                                  e.stopPropagation()
                                  if (!jumpUrl) return
                                  void openExternalUrl(jumpUrl)
                                }}
                                className={[
                                  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold transition-colors',
                                  jumpUrl
                                    ? 'border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100'
                                    : 'cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400',
                                ].join(' ')}
                                title={jumpUrl ? 'Open original video at this time' : undefined}
                                disabled={!jumpUrl}
                              >
                                <Clock className="h-3.5 w-3.5" />
                                {normalized || children}
                              </button>
                            )
                          }

                          if (rawHref.startsWith('#')) {
                            return (
                              <a
                                href={href}
                                onClick={e => e.stopPropagation()}
                                className="text-primary hover:text-primary/80 font-medium underline underline-offset-4"
                                {...props}
                              >
                                {children}
                              </a>
                            )
                          }

                          return (
                            <a
                              href={href}
                              onClick={e => {
                                e.preventDefault()
                                e.stopPropagation()
                                if (href) void openExternalUrl(href)
                              }}
                              className="text-primary hover:text-primary/80 font-medium underline underline-offset-4"
                              {...props}
                            >
                              {children}
                            </a>
                          )
                        },
                        strong: ({ children, ...props }) => (
                          <strong className="font-semibold text-slate-900" {...props}>
                            {children}
                          </strong>
                        ),
                        code: ({ className, children, ...props }) => {
                          const isInline = !className
                          if (isInline) {
                            return (
                              <code
                                className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] text-slate-800"
                                {...props}
                              >
                                {children}
                              </code>
                            )
                          }
                          return (
                            <code className={className} {...props}>
                              {children}
                            </code>
                          )
                        },
                      }}
                    >
                      {renderedMarkdown}
                    </ReactMarkdown>
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => (currentConversationId ? setSelectedReferenceMessage(currentConversationId, m.id) : undefined)}
                      className={[
                        'text-xs px-2 py-1 rounded border transition-colors',
                        isSelected
                          ? 'bg-primary-light text-primary border-primary/30'
                          : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200',
                      ].join(' ')}
                      title="点击查看该条回复的引用"
                    >
                      {m.resources && m.resources.length > 0 ? `引用：${m.resources.length} 条` : '无引用'}
                    </button>
                  </div>
                </div>
              </div>
            )
          })
        )}
        {sending && (
          <div className="flex gap-4 max-w-3xl mx-auto">
            <div className="w-8 h-8 rounded-full bg-primary flex-shrink-0 flex items-center justify-center text-primary-foreground text-xs font-bold">
              AI
            </div>
            <div className="space-y-2 w-full">
              <div className="bg-white p-4 rounded-2xl rounded-tl-none border border-slate-200 shadow-sm text-slate-700 text-sm leading-relaxed">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                  <span className="text-slate-500">正在检索知识库并生成回答…</span>
                </div>
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="p-6 bg-white border-t border-slate-200">
        <div className="max-w-3xl mx-auto relative">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (canSend) void send()
              }
            }}
            className="w-full pl-4 pr-12 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-sm resize-none shadow-inner text-slate-900 placeholder:text-slate-400"
            placeholder="关于视频提问（Shift+Enter 换行）..."
            rows={1}
            style={{ minHeight: '50px' }}
            disabled={sending}
          />
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            className="absolute right-2 bottom-2 p-2 bg-primary hover:opacity-95 disabled:opacity-50 disabled:hover:opacity-50 text-primary-foreground rounded-lg transition-colors shadow-sm"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
        <p className="text-center text-xs text-slate-400 mt-2">AI 可能会犯错，请核对重要信息。</p>
      </div>
    </div>
  )
}

export default RagChatPanel
