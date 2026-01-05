const STOPWORDS = [
  // Generic query words (ZH)
  '哪里',
  '在哪',
  '在哪里',
  '怎么',
  '如何',
  '为什么',
  '是否',
  '有没有',
  '有无',
  '能不能',
  '可以吗',
  '请问',
  '帮我',
  '帮忙',
  '给我',
  '一下',
  '这个',
  '这段',
  '内容',
  '视频',
  '知识库',
  '讲到',
  '提到',
  '说到',
  '提及',
  '定位',
  '时间戳',
  '链接',
  '原文',
  '片段',
  '字幕',
  '笔记',
  // Generic query words (EN)
  'where',
  'what',
  'how',
  'why',
  'timestamp',
  'timecode',
  'link',
  'video',
  'knowledge',
]

const normalize = (input: string) => {
  return String(input || '')
    .trim()
    .replace(/\s+/g, ' ')
}

const uniq = (items: string[]) => {
  const out: string[] = []
  const seen = new Set<string>()
  for (const item of items) {
    const key = item.toLowerCase()
    if (!item || seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }
  return out
}

const splitByStopwords = (token: string) => {
  let t = String(token || '').trim()
  if (!t) return []

  for (const sw of STOPWORDS) {
    if (!sw) continue
    t = t.split(sw).join(' ')
  }
  return t
    .split(/\s+/g)
    .map(s => s.trim())
    .filter(Boolean)
}

/**
 * Extract user-intent keywords for highlighting references.
 *
 * Heuristic-only (no NLP dependency): works best for short Chinese/English queries.
 */
export const extractQueryKeywords = (query: string, maxKeywords = 8): string[] => {
  const q = normalize(query)
  if (!q) return []

  // Pull Chinese runs and ASCII words.
  const rawTokens = [
    ...q.matchAll(/[\u4e00-\u9fff]{2,}/g),
    ...q.matchAll(/[A-Za-z0-9]{2,}/g),
  ].map(m => m[0])

  const cleaned: string[] = []
  for (const token of rawTokens) {
    const parts = splitByStopwords(token)
    for (const part of parts) {
      const p = part.trim()
      if (!p) continue
      if (p.length < 2) continue
      if (STOPWORDS.includes(p) || STOPWORDS.includes(p.toLowerCase())) continue
      cleaned.push(p)
    }
  }

  // Prefer longer phrases to avoid highlighting tiny substrings.
  const ordered = uniq(cleaned).sort((a, b) => b.length - a.length)
  return ordered.slice(0, Math.max(0, maxKeywords))
}

