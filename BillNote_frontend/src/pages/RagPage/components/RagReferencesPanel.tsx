import { useMemo } from 'react'

import { useRagStore } from '@/store/ragStore'
import { openExternalUrl } from '@/utils'

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

  if (p === 'bilibili') return `https://www.bilibili.com/video/${v}`
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

const RagReferencesPanel = () => {
  const conversations = useRagStore(state => state.conversations)
  const currentConversationId = useRagStore(state => state.currentConversationId)

  const currentConversation = useMemo(() => {
    return conversations.find(c => c.id === currentConversationId) || null
  }, [conversations, currentConversationId])

  const lastResources = useMemo(() => {
    const messages = currentConversation?.messages || []
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.role === 'assistant' && m.resources && m.resources.length > 0) return m.resources
    }
    return []
  }, [currentConversation])

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

        <div className="space-y-3">
          {lastResources.length === 0 ? (
            <div className="p-4 rounded-lg border border-slate-200 bg-white text-sm text-slate-500">
              <div className="font-medium text-slate-700">暂无引用</div>
              <ul className="list-disc list-inside mt-2 space-y-1 text-slate-600">
                <li>先在左侧点击“生成笔记并入库”，并等待状态变为“已入库”。</li>
                <li>如果你在 Dify 里删过/换过 Dataset，确认 Dify App 也绑定的是同一个 Dataset。</li>
                <li>仍无引用时，检查 Dify App 是否启用 Knowledge 检索并已发布（Publish）。</li>
              </ul>
            </div>
          ) : (
            lastResources.map(r => {
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
                      <div className="mt-1 text-sm font-medium text-slate-800 truncate">{r.document_name}</div>
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
                    {snippet}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </>
  )
}

export default RagReferencesPanel
