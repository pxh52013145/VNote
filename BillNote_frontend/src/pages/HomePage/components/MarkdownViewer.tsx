import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/ui/button.tsx'
import { Copy, Download, ArrowRight, Play, ExternalLink, X } from 'lucide-react'
import { toast } from 'react-hot-toast'
import Error from '@/components/Lottie/error.tsx'
import Loading from '@/components/Lottie/Loading.tsx'
import Idle from '@/components/Lottie/Idle.tsx'
import StepBar from '@/pages/HomePage/components/StepBar.tsx'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomDark as codeStyle } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'
import gfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeSlug from 'rehype-slug'
import 'katex/dist/katex.min.css'
import 'github-markdown-css/github-markdown-light.css'
import { FC } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { useTaskStore } from '@/store/taskStore'
import { noteStyles } from '@/constant/note.ts'
import { MarkdownHeader } from '@/pages/HomePage/components/MarkdownHeader.tsx'
import TranscriptViewer from '@/pages/HomePage/components/transcriptViewer.tsx'
import MarkmapEditor from '@/pages/HomePage/components/MarkmapComponent.tsx'

interface VersionNote {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at?: string
}

interface MarkdownViewerProps {
  content: string | VersionNote[]
  status: 'idle' | 'loading' | 'success' | 'failed'
}

const steps = [
  { label: '排队', key: 'PENDING' },
  { label: '解析链接', key: 'PARSING' },
  { label: '下载音频', key: 'DOWNLOADING' },
  { label: '转写文字', key: 'TRANSCRIBING' },
  { label: '总结内容', key: 'SUMMARIZING' },
  { label: '格式化', key: 'FORMATTING' },
  { label: '保存', key: 'SAVING' },
  { label: '完成', key: 'SUCCESS' },
]

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
  }
  return map[status] || status
}

const getStatusProgress = (status: string) => {
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
  }
  const v = map[status] ?? 0
  return Math.max(0, Math.min(100, Math.round(v)))
}

const parseTimestampToSeconds = (text: string): number | null => {
  const trimmed = (text || '').trim()
  if (!trimmed) return null

  const parts = trimmed.split(':').map(p => p.trim())
  if (parts.length !== 2 && parts.length !== 3) return null

  const nums = parts.map(p => Number(p))
  if (nums.some(n => !Number.isFinite(n) || n < 0)) return null

  if (nums.length === 2) {
    const [minutes, seconds] = nums
    if (seconds >= 60) return null
    return Math.floor(minutes * 60 + seconds)
  }

  const [hours, minutes, seconds] = nums
  if (minutes >= 60 || seconds >= 60) return null
  return Math.floor(hours * 3600 + minutes * 60 + seconds)
}

const MarkdownViewer: FC<MarkdownViewerProps> = ({ status }) => {
  const [copied, setCopied] = useState(false)
  const [currentVerId, setCurrentVerId] = useState<string>('')
  const [selectedContent, setSelectedContent] = useState<string>('')
  const [modelName, setModelName] = useState<string>('')
  const [style, setStyle] = useState<string>('')
  const [createTime, setCreateTime] = useState<string>('')
  const markdownScrollAreaRef = useRef<HTMLDivElement>(null)
  const [transcriptSeekSeconds, setTranscriptSeekSeconds] = useState<number>(0)
  const [transcriptSeekNonce, setTranscriptSeekNonce] = useState<number>(0)
  // 确保baseURL没有尾部斜杠
  const baseURL = (String(import.meta.env.VITE_API_BASE_URL || '').replace('/api','') || '').replace(/\/$/, '')
  const getCurrentTask = useTaskStore.getState().getCurrentTask
  const currentTask = useTaskStore(state => state.getCurrentTask())
  const taskStatus = currentTask?.status || 'PENDING'
  const taskProgress =
    typeof currentTask?.progress === 'number' && Number.isFinite(currentTask.progress)
      ? Math.max(0, Math.min(100, Math.round(currentTask.progress)))
      : getStatusProgress(taskStatus)
  const retryTask = useTaskStore.getState().retryTask
  const isMultiVersion = Array.isArray(currentTask?.markdown)
  const [showTranscribe, setShowTranscribe] = useState(false)
  const [viewMode, setViewMode] = useState<'map' | 'preview'>('preview')
  const svgRef = useRef<SVGSVGElement>(null)

  const getCurrentMarkdownTimestampSeconds = () => {
    const viewport = markdownScrollAreaRef.current?.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]'
    )
    if (!viewport) return 0

    const markers = Array.from(
      viewport.querySelectorAll<HTMLElement>('[data-origin-time-seconds]')
    )
    if (markers.length === 0) return 0

    const viewportRectTop = viewport.getBoundingClientRect().top
    const anchorY = viewport.scrollTop + viewport.clientHeight * 0.25

    let pickedSeconds = 0
    let pickedTop = -Infinity
    for (const marker of markers) {
      const seconds = Number(marker.dataset.originTimeSeconds)
      if (!Number.isFinite(seconds)) continue

      const top = marker.getBoundingClientRect().top - viewportRectTop + viewport.scrollTop
      if (top <= anchorY && top >= pickedTop) {
        pickedSeconds = seconds
        pickedTop = top
      }
    }

    return pickedSeconds
  }

  const handleShowTranscribeChange = (open: boolean) => {
    if (open) {
      setTranscriptSeekSeconds(getCurrentMarkdownTimestampSeconds())
      setTranscriptSeekNonce(n => n + 1)
    }
    setShowTranscribe(open)
  }

  const renderTranscriptPanel = () => {
    if (!showTranscribe) return null
    if (typeof document === 'undefined') return null

    const dockEl = document.getElementById('transcript-dock')
    const portalTarget = dockEl ?? document.body
    const docked = Boolean(dockEl)

    return createPortal(
      <div
        className={
          docked
            ? 'h-full w-full'
            : 'fixed left-0 top-0 z-50 h-screen w-80 bg-white shadow-xl lg:w-96'
        }
      >
        <div className="relative h-full w-full">
          <button
            type="button"
            aria-label="关闭原文参照"
            onClick={() => handleShowTranscribeChange(false)}
            className="absolute right-3 top-3 z-10 rounded-md bg-white/90 p-1.5 text-slate-600 shadow-sm ring-1 ring-slate-200 hover:bg-white hover:text-slate-900"
          >
            <X className="h-4 w-4" />
          </button>
          <TranscriptViewer seekSeconds={transcriptSeekSeconds} seekNonce={transcriptSeekNonce} />
        </div>
      </div>,
      portalTarget
    )
  }
  // 多版本内容处理
  useEffect(() => {
    if (!currentTask) return

    if (!isMultiVersion) {
      setCurrentVerId('') // 清空旧版本 ID
      setModelName(currentTask.formData.model_name)
      setStyle(currentTask.formData.style)
      setCreateTime(currentTask.createdAt)
      setSelectedContent(currentTask?.markdown)
    } else {
      const latestVersion = [...currentTask.markdown].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )[0]

      if (latestVersion) {
        setCurrentVerId(latestVersion.ver_id)
      }
    }
  }, [currentTask?.id, taskStatus])
  useEffect(() => {
    if (!currentTask || !isMultiVersion) return

    const currentVer = currentTask.markdown.find(v => v.ver_id === currentVerId)
    if (currentVer) {
      setModelName(currentVer.model_name)
      setStyle(currentVer.style)
      setCreateTime(currentVer.created_at || '')
      setSelectedContent(currentVer.content)
    }
  }, [currentVerId, currentTask?.id])
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(selectedContent)
      setCopied(true)
      toast.success('已复制到剪贴板')
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      toast.error('复制失败')
    }
  }
  const alertButton = {
    id: 'alert',
    title: '测试警告',
    content: '⚠️',
    onClick: () => alert('你点击了自定义按钮！'),
  }
  const exportButton = {
    id: 'export',
    title: '导出思维导图',
    content: '⤓',
    onClick: () => {
      const svgEl = svgRef.current
      if (!svgEl) return
      // 同上面的序列化逻辑
      const serializer = new XMLSerializer()
      const source = serializer.serializeToString(svgEl)
      const blob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>', source], {
        type: 'image/svg+xml;charset=utf-8',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'mindmap.svg'
      a.click()
      URL.revokeObjectURL(url)
    },
  }
  const handleDownload = () => {
    const task = getCurrentTask()
    const name = task?.audioMeta.title || 'note'
    const blob = new Blob([selectedContent], { type: 'text/markdown;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${name}.md`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  if (status === 'loading') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center space-y-4 text-neutral-500">
        <StepBar steps={steps} currentStep={taskStatus} />
        <div className="w-full max-w-md px-6">
          <div className="flex items-center justify-between text-xs text-neutral-600 dark:text-neutral-300">
            <span className="line-clamp-1">{getStatusLabel(taskStatus)}</span>
            <span className="tabular-nums">{taskProgress}%</span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded bg-neutral-200 dark:bg-neutral-800">
            <div
              className="h-full bg-primary transition-[width] duration-300"
              style={{ width: `${taskProgress}%` }}
            />
          </div>
        </div>
        <Loading className="h-5 w-5" />
        <div className="text-center text-sm">
          <p className="text-lg font-bold">正在生成笔记，请稍候…</p>
          <p className="mt-2 text-xs text-neutral-500">这可能需要几秒钟时间，取决于视频长度</p>
        </div>
      </div>
    )
  }

  if (status === 'idle') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center space-y-3 text-neutral-500">
        <Idle />
        <div className="text-center">
          <p className="text-lg font-bold">输入视频链接并点击“生成笔记”</p>
          <p className="mt-2 text-xs text-neutral-500">支持哔哩哔哩、YouTube 、抖音等视频平台</p>
        </div>
      </div>
    )
  }

  if (status === 'failed' && !isMultiVersion) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-4 space-y-3">
        <Error />
        <div className="text-center">
          <p className="text-lg font-bold text-red-500">笔记生成失败</p>
          <p className="mt-2 mb-2 text-xs text-red-400">请检查后台或稍后再试</p>

          <Button onClick={() => retryTask(currentTask.id)} size="lg">
            重试
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <MarkdownHeader
        currentTask={currentTask}
        isMultiVersion={isMultiVersion}
        currentVerId={currentVerId}
        setCurrentVerId={setCurrentVerId}
        modelName={modelName}
        style={style}
        noteStyles={noteStyles}
        onCopy={handleCopy}
        onDownload={handleDownload}
        createAt={createTime}
        showTranscribe={showTranscribe}
        setShowTranscribe={handleShowTranscribeChange}
        viewMode={viewMode}
        setViewMode={setViewMode}
      />
      {renderTranscriptPanel()}

      {viewMode === 'map' ? (
        <div className="flex w-full flex-1 overflow-hidden bg-white">
          <div className={'w-full'}>
            <MarkmapEditor
              value={selectedContent}
              onChange={() => {}}
              height="100%" // 根据需求可以设定百分比或固定高度
              title={currentTask?.audioMeta?.title || '思维导图'}
            />
          </div>
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden bg-white py-2">
          {selectedContent && selectedContent !== 'loading' && selectedContent !== 'empty' ? (
            <>
              <ScrollArea ref={markdownScrollAreaRef} className="w-full">
                <div className={'markdown-body w-full px-2'}>
                  <ReactMarkdown
                    remarkPlugins={[gfm, remarkMath]}
                    rehypePlugins={[rehypeKatex, rehypeSlug]}
                    components={{
                      // Headings with improved styling and anchor links
                      h1: ({ children, ...props }) => (
                        <h1
                          className="text-primary my-6 scroll-m-20 text-3xl font-extrabold tracking-tight lg:text-4xl"
                          {...props}
                        >
                          {children}
                        </h1>
                      ),
                      h2: ({ children, ...props }) => (
                        <h2
                          className="text-primary mt-10 mb-4 scroll-m-20 border-b pb-2 text-2xl font-semibold tracking-tight first:mt-0"
                          {...props}
                        >
                          {children}
                        </h2>
                      ),
                      h3: ({ children, ...props }) => (
                        <h3
                          className="text-primary mt-8 mb-4 scroll-m-20 text-xl font-semibold tracking-tight"
                          {...props}
                        >
                          {children}
                        </h3>
                      ),
                      h4: ({ children, ...props }) => (
                        <h4
                          className="text-primary mt-6 mb-2 scroll-m-20 text-lg font-semibold tracking-tight"
                          {...props}
                        >
                          {children}
                        </h4>
                      ),

                      // Paragraphs with better line height
                      p: ({ children, ...props }) => (
                        <p className="leading-7 [&:not(:first-child)]:mt-6" {...props}>
                          {children}
                        </p>
                      ),

                      // Enhanced links with special handling for "原片" links
                      a: ({ href, children, onClick, ...props }) => {
                        const isHashLink = typeof href === 'string' && href.startsWith('#') && href.length > 1
                        const getPlainText = (node: any): string => {
                          if (node == null) return ''
                          if (typeof node === 'string' || typeof node === 'number') return String(node)
                          if (Array.isArray(node)) return node.map(getPlainText).join('')
                          if (typeof node === 'object' && 'props' in node) return getPlainText((node as any).props?.children)
                          return ''
                        }
                        const normalizeText = (text: string) =>
                          (text || '')
                            .toLowerCase()
                            .replace(/\s+/g, ' ')
                            .trim()

                        const originText = getPlainText(children)
                        const originTimeMatch = originText.match(/原片\s*@\s*(\d{1,2}:\d{2}(?::\d{2})?)/)

                        if (originTimeMatch) {
                          const timeText = originTimeMatch[1]
                          const timeSeconds = parseTimestampToSeconds(timeText) ?? 0

                          return (
                            <span
                              className="origin-link my-2 inline-flex"
                              data-origin-time-seconds={timeSeconds}
                            >
                              <a
                                href={href}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100"
                                {...props}
                              >
                                <Play className="h-3.5 w-3.5" />
                                <span>原片{timeText ? `（${timeText}）` : ''}</span>
                              </a>
                            </span>
                          )
                        }

                        if (isHashLink) {
                          return (
                            <a
                              className="text-primary hover:text-primary/80 inline-flex items-center gap-0.5 font-medium underline underline-offset-4"
                              onClick={e => {
                                onClick?.(e)
                                e.preventDefault()
                                let id = href.slice(1)
                                try {
                                  id = decodeURIComponent(id)
                                } catch {
                                  // ignore
                                }

                                const byId = document.getElementById(id)
                                if (byId) {
                                  byId.scrollIntoView({ behavior: 'smooth', block: 'start' })
                                } else {
                                  const linkText = normalizeText(getPlainText(children))
                                  if (linkText) {
                                    const headings = Array.from(
                                      document.querySelectorAll('.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4, .markdown-body h5, .markdown-body h6')
                                    ) as HTMLElement[]
                                    const picked = headings.find(h => normalizeText(h.textContent || '').startsWith(linkText))
                                      || headings.find(h => normalizeText(h.textContent || '').includes(linkText))
                                    picked?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                                  }
                                }
                                try {
                                  window.history.replaceState(null, '', href)
                                } catch {
                                  // ignore
                                }
                              }}
                              href={href}
                              {...props}
                            >
                              {children}
                            </a>
                          )
                        }

                        // Default link styling with external indicator
                        return (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:text-primary/80 inline-flex items-center gap-0.5 font-medium underline underline-offset-4"
                            {...props}
                          >
                            {children}
                            {href?.startsWith('http') && (
                              <ExternalLink className="ml-0.5 inline-block h-3 w-3" />
                            )}
                          </a>
                        )
                      },

                      // Enhanced image with zoom capability
                      img: ({ node, ...props }) =>{
                        // Fix the URL by removing the 'undefined' prefix if it exists
                        let src = props.src
                        if (src.startsWith('/')) {
                          src = baseURL + src
                        }
                        props.src = src

                     return(
                      <div className="my-8 flex justify-center">
                          <Zoom>
                            <img
                              {...props}
                              className="max-w-full cursor-zoom-in rounded-lg object-cover shadow-md transition-all hover:shadow-lg"
                              style={{ maxHeight: '500px' }}
                            />
                          </Zoom>
                        </div>
                      )},

                      // Better strong/bold text
                      strong: ({ children, ...props }) => (
                        <strong className="text-primary font-bold" {...props}>
                          {children}
                        </strong>
                      ),

                      // Enhanced list items with support for "fake headings"
                      li: ({ children, ...props }) => {
                        const rawText = String(children)
                        const isFakeHeading = /^(\*\*.+\*\*)$/.test(rawText.trim())

                        if (isFakeHeading) {
                          return (
                            <div className="text-primary my-4 text-lg font-bold">{children}</div>
                          )
                        }

                        return (
                          <li className="my-1" {...props}>
                            {children}
                          </li>
                        )
                      },

                      // Enhanced unordered lists
                      ul: ({ children, ...props }) => (
                        <ul className="my-6 ml-6 list-disc [&>li]:mt-2" {...props}>
                          {children}
                        </ul>
                      ),

                      // Enhanced ordered lists
                      ol: ({ children, ...props }) => (
                        <ol className="my-6 ml-6 list-decimal [&>li]:mt-2" {...props}>
                          {children}
                        </ol>
                      ),

                      // Enhanced blockquotes
                      blockquote: ({ children, ...props }) => (
                        <blockquote
                          className="border-primary/20 text-muted-foreground mt-6 border-l-4 pl-4 italic"
                          {...props}
                        >
                          {children}
                        </blockquote>
                      ),

                      // Enhanced code blocks with syntax highlighting and copy button
                      code: ({ inline, className, children, ...props }) => {
                        const match = /language-(\w+)/.exec(className || '')
                        const codeContent = String(children).replace(/\n$/, '')

                        if (!inline && match) {
                          return (
                            <div className="group bg-muted relative my-6 overflow-hidden rounded-lg border shadow-sm">
                              <div className="bg-muted text-muted-foreground flex items-center justify-between px-4 py-1.5 text-sm font-medium">
                                <div>{match[1].toUpperCase()}</div>
                                <button
                                  onClick={() => {
                                    navigator.clipboard.writeText(codeContent)
                                    toast.success('代码已复制')
                                  }}
                                  className="bg-background/80 hover:bg-background flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors"
                                >
                                  <Copy className="h-3.5 w-3.5" />
                                  复制
                                </button>
                              </div>
                              <SyntaxHighlighter
                                style={codeStyle}
                                language={match[1]}
                                PreTag="div"
                                className="!bg-muted !m-0 !p-0"
                                customStyle={{
                                  margin: 0,
                                  padding: '1rem',
                                  background: 'transparent',
                                  fontSize: '0.9rem',
                                }}
                                {...props}
                              >
                                {codeContent}
                              </SyntaxHighlighter>
                            </div>
                          )
                        }

                        // Inline code styling
                        return (
                          <code
                            className="bg-muted relative rounded px-[0.3rem] py-[0.2rem] font-mono text-sm"
                            {...props}
                          >
                            {children}
                          </code>
                        )
                      },

                      // Enhanced tables
                      table: ({ children, ...props }) => (
                        <div className="my-6 w-full overflow-y-auto">
                          <table className="w-full border-collapse text-sm" {...props}>
                            {children}
                          </table>
                        </div>
                      ),

                      // Table headers
                      th: ({ children, ...props }) => (
                        <th
                          className="border-muted-foreground/20 border px-4 py-2 text-left font-medium [&[align=center]]:text-center [&[align=right]]:text-right"
                          {...props}
                        >
                          {children}
                        </th>
                      ),

                      // Table cells
                      td: ({ children, ...props }) => (
                        <td
                          className="border-muted-foreground/20 border px-4 py-2 text-left [&[align=center]]:text-center [&[align=right]]:text-right"
                          {...props}
                        >
                          {children}
                        </td>
                      ),

                      // Horizontal rule
                      hr: ({ ...props }) => (
                        <hr className="border-muted-foreground/20 my-8" {...props} />
                      ),
                    }}
                  >
                    {selectedContent}
                  </ReactMarkdown>
                </div>
              </ScrollArea>
            </>
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <div className="w-[300px] flex-col justify-items-center">
                <div className="bg-primary-light mb-4 flex h-16 w-16 items-center justify-center rounded-full">
                  <ArrowRight className="text-primary h-8 w-8" />
                </div>
                <p className="mb-2 text-neutral-600">输入视频链接并点击"生成笔记"按钮</p>
                <p className="text-xs text-neutral-500">支持哔哩哔哩、YouTube等视频网站</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default MarkdownViewer
