/* NoteForm.tsx ---------------------------------------------------- */
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form.tsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { type FieldErrors, useFieldArray, useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

import { CheckCircle2, Info, Loader2, PauseCircle, Plus, X, XCircle } from 'lucide-react'
import { Alert, Popover } from 'antd'
import toast from 'react-hot-toast'
import { generateNote } from '@/services/note.ts'
import { uploadFile } from '@/services/upload.ts'
import { type Task, useTaskStore } from '@/store/taskStore'
import { useModelStore } from '@/store/modelStore'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip.tsx'
import { Checkbox } from '@/components/ui/checkbox.tsx'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { Button } from '@/components/ui/button.tsx'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select.tsx'
import { Input } from '@/components/ui/input.tsx'
import { Textarea } from '@/components/ui/textarea.tsx'
import { noteStyles, noteFormats, videoPlatforms } from '@/constant/note.ts'
import { useNavigate } from 'react-router-dom'

/* -------------------- 校验 Schema -------------------- */
const formSchema = z
  .object({
    video_urls: z.array(z.string()).default([]),
    platform: z.string().nonempty('请选择平台'),
    quality: z.enum(['fast', 'medium', 'slow']),
    screenshot: z.boolean().optional(),
    link: z.boolean().optional(),
    model_name: z.string().nonempty('请选择模型'),
    format: z.array(z.string()).default([]),
    style: z.string().nonempty('请选择笔记生成风格'),
    extras: z.string().optional(),
    video_understanding: z.boolean().optional(),
    video_interval: z.coerce.number().min(1).max(30).default(4).optional(),
    grid_size: z
      .tuple([z.coerce.number().min(1).max(10), z.coerce.number().min(1).max(10)])
      .default([3, 3])
      .optional(),
  })
  .superRefine(({ video_urls, platform }, ctx) => {
    const cleaned = (Array.isArray(video_urls) ? video_urls : []).map(v => String(v ?? '').trim())
    const entries = cleaned.filter(Boolean)

    if (entries.length === 0) {
      ctx.addIssue({
        code: 'custom',
        message: platform === 'local' ? '本地视频路径不能为空' : '视频链接不能为空',
        path: ['video_urls', 0],
      })
      return
    }

    if (platform === 'local') {
      if (entries.length > 1) {
        ctx.addIssue({ code: 'custom', message: '本地视频暂不支持批量导入', path: ['video_urls'] })
      }
      return
    } else {
      for (let i = 0; i < cleaned.length; i += 1) {
        const raw = cleaned[i]
        if (!raw) continue
        try {
          const url = new URL(raw)
          if (!['http:', 'https:'].includes(url.protocol)) throw new Error()
        } catch {
          ctx.addIssue({ code: 'custom', message: '请输入正确的视频链接', path: ['video_urls', i] })
        }
      }
    }
  })

export type NoteFormValues = z.infer<typeof formSchema>

/* -------------------- 可复用子组件 -------------------- */
const SectionHeader = ({ title, tip }: { title: string; tip?: string }) => (
  <div className="my-3 flex items-center justify-between">
    <h2 className="block">{title}</h2>
    {tip && (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Info className="hover:text-primary h-4 w-4 cursor-pointer text-neutral-400" />
          </TooltipTrigger>
          <TooltipContent className="text-xs">{tip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )}
  </div>
)

const CheckboxGroup = ({
  value = [],
  onChange,
  disabledMap,
}: {
  value?: string[]
  onChange: (v: string[]) => void
  disabledMap: Record<string, boolean>
}) => (
  <div className="flex flex-wrap space-x-1.5">
    {noteFormats.map(({ label, value: v }) => (
      <label key={v} className="flex items-center space-x-2">
        <Checkbox
          checked={value.includes(v)}
          disabled={disabledMap[v]}
          onCheckedChange={checked =>
            onChange(checked ? [...value, v] : value.filter(x => x !== v))
          }
        />
        <span>{label}</span>
      </label>
    ))}
  </div>
)

/* -------------------- 主组件 -------------------- */
type DuplicateStrategy = 'ask' | 'skip' | 'regenerate'
type BatchItemStatus = 'queued' | 'running' | 'success' | 'failed' | 'skipped'

interface BatchItem {
  id: string
  url: string
  platform: string
  status: BatchItemStatus
  taskId?: string
  error?: string
}

const inferPlatformFromUrl = (defaultPlatform: string, url: string, enabled: boolean) => {
  if (!enabled) return defaultPlatform
  const u = String(url || '').toLowerCase()
  if (u.includes('bilibili.com')) return 'bilibili'
  if (u.includes('youtube.com') || u.includes('youtu.be')) return 'youtube'
  if (u.includes('douyin.com')) return 'douyin'
  if (u.includes('kuaishou.com')) return 'kuaishou'
  return defaultPlatform
}

const safeUrl = (raw: string) => {
  try {
    return new URL(raw)
  } catch {
    return null
  }
}

const buildSourceKeyFromUrl = (platform: string, rawUrl: string) => {
  const p = String(platform || '').toLowerCase()
  const raw = String(rawUrl || '').trim()
  if (!raw) return null

  if (p === 'local') return `local:${raw}`

  const parsed = safeUrl(raw)
  if (!parsed) return `${p}:${raw}`

  // ignore timestamps and other noisy params
  parsed.hash = ''
  parsed.searchParams.delete('t')
  parsed.searchParams.delete('start')

  if (p === 'bilibili') {
    const bv = /BV[0-9A-Za-z]+/i.exec(parsed.pathname)?.[0] || /BV[0-9A-Za-z]+/i.exec(raw)?.[0]
    const part = parsed.searchParams.get('p')
    if (bv) return `bilibili:${bv}${part ? `_p${part}` : ''}`
  }

  if (p === 'youtube') {
    const v = parsed.searchParams.get('v') || ''
    const short = parsed.hostname.includes('youtu.be') ? parsed.pathname.replace(/^\/+/, '') : ''
    const vid = v || short
    if (vid) return `youtube:${vid}`
  }

  return `${p}:${parsed.toString()}`
}

const buildSourceKeyFromTask = (task: Task) => {
  const platform = String(task?.platform || task?.formData?.platform || '').toLowerCase()
  const vid = String(task?.audioMeta?.video_id || '').trim()
  if (platform && vid) return `${platform}:${vid}`
  if (task?.formData?.video_url) return buildSourceKeyFromUrl(platform, task.formData.video_url)
  return null
}

const NoteForm = () => {
  const navigate = useNavigate();
  const [isUploading, setIsUploading] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState(false)
  /* ---- 全局状态 ---- */
  const { tasks, addPendingTask, ingestTaskId, setIngestTask, getIngestTask, retryTask } =
    useTaskStore()
  const { loadEnabledModels, modelList, showFeatureHint, setShowFeatureHint } = useModelStore()

  /* ---- 表单 ---- */
  const form = useForm<NoteFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      platform: 'bilibili',
      video_urls: [''],
      quality: 'medium',
      screenshot: false,
      link: false,
      model_name: modelList[0]?.model_name || '',
      style: 'minimal',
      extras: '',
      video_understanding: false,
      video_interval: 4,
      grid_size: [3, 3],
      format: [],
    },
  })
  const currentTask = getIngestTask()
  const { fields: videoUrlFields, append: appendVideoUrl, remove: removeVideoUrl } = useFieldArray({
    control: form.control,
    name: 'video_urls',
  })
  const watchedVideoUrls = useWatch({ control: form.control, name: 'video_urls' }) as string[]
  const cleanedVideoUrls = useMemo(() => {
    return (Array.isArray(watchedVideoUrls) ? watchedVideoUrls : [])
      .map(v => String(v ?? '').trim())
      .filter(Boolean)
  }, [watchedVideoUrls])

  const [autoDetectPlatform, setAutoDetectPlatform] = useState(true)
  const [duplicateStrategy, setDuplicateStrategy] = useState<DuplicateStrategy>('ask')

  const [batchItems, setBatchItems] = useState<BatchItem[]>([])
  const [batchRunning, setBatchRunning] = useState(false)
  const [stopAfterCurrent, setStopAfterCurrent] = useState(false)
  const [advancedPopover, setAdvancedPopover] = useState<'video' | 'format' | 'extras' | null>(null)
  const stopAfterCurrentRef = useRef(false)
  useEffect(() => {
    stopAfterCurrentRef.current = stopAfterCurrent
  }, [stopAfterCurrent])

  /* ---- 派生状态（只 watch 一次，提高性能） ---- */
  const platform = useWatch({ control: form.control, name: 'platform' }) as string
  const videoUnderstandingEnabled = useWatch({ control: form.control, name: 'video_understanding' })
  const editing = currentTask && currentTask.id

  const goModelAdd = () => {
    navigate("/settings/model");
  };
  /* ---- 副作用 ---- */
  useEffect(() => {
    loadEnabledModels()

    return
  }, [])
  useEffect(() => {
    const defaults = {
      platform: 'bilibili',
      quality: 'medium' as const,
      video_urls: [''],
      model_name: modelList[0]?.model_name || '',
      style: 'minimal',
      extras: '',
      screenshot: false,
      link: false,
      video_understanding: false,
      video_interval: 4,
      grid_size: [3, 3] as [number, number],
      format: [] as string[],
    }

    // No selected task (e.g. app start) -> always show a fresh form.
    if (!ingestTaskId) {
      setUploadSuccess(false)
      form.reset(defaults)
      return
    }

    if (!currentTask) return
    const { formData } = currentTask

    form.reset({
      ...defaults,
      // ensure fallbacks
      platform: formData.platform || defaults.platform,
      video_urls: [formData.video_url || defaults.video_urls[0] || ''],
      model_name: formData.model_name || defaults.model_name,
      style: formData.style || defaults.style,
      quality: (formData.quality as any) || defaults.quality,
      extras: formData.extras || defaults.extras,
      screenshot: formData.screenshot ?? defaults.screenshot,
      link: formData.link ?? defaults.link,
      video_understanding: formData.video_understanding ?? defaults.video_understanding,
      video_interval: formData.video_interval ?? defaults.video_interval,
      grid_size: (formData.grid_size as any) ?? defaults.grid_size,
      format: formData.format ?? defaults.format,
    })
  }, [
    // 当下面任意一个变了，就重新 reset
    ingestTaskId,
    // modelList 用来兜底 model_name
    modelList.length,
  ])

  /* ---- 帮助函数 ---- */
  const isGenerating = () => !['SUCCESS', 'FAILED', 'CANCELLED', undefined].includes(getIngestTask()?.status)
  const generating = batchRunning || isGenerating()
  const handleFileUpload = async (file: File, cb: (url: string) => void) => {
    const formData = new FormData()
    formData.append('file', file)
    setIsUploading(true)
    setUploadSuccess(false)

    try {
  
      const  data  = await uploadFile(formData)
        cb(data.url)
        setUploadSuccess(true)
    } catch (err) {
      console.error('上传失败:', err)
      // message.error('上传失败，请重试')
    } finally {
      setIsUploading(false)
    }
  }

  const updateBatchItem = (id: string, patch: Partial<BatchItem>) => {
    setBatchItems(prev => prev.map(item => (item.id === id ? { ...item, ...patch } : item)))
  }

  const resetBatch = () => {
    if (batchRunning) return
    setBatchItems([])
    setStopAfterCurrent(false)
  }

  const onSubmit = async (values: NoteFormValues) => {
    if (batchRunning) return

    const providerId = modelList.find(m => m.model_name === values.model_name)?.provider_id || ''
    if (!providerId) {
      toast.error('请先选择可用模型')
      return
    }

    const urls = (Array.isArray(values.video_urls) ? values.video_urls : [])
      .map(v => String(v ?? '').trim())
      .filter(Boolean)

    if (urls.length === 0) {
      toast.error(values.platform === 'local' ? '本地视频路径不能为空' : '视频链接不能为空')
      return
    }

    const buildFormData = (video_url: string, platform: string) => ({
      video_url,
      platform,
      quality: values.quality,
      model_name: values.model_name,
      provider_id: providerId,
      format: values.format,
      style: values.style,
      extras: values.extras,
      link: values.link,
      screenshot: values.screenshot,
      video_understanding: values.video_understanding,
      video_interval: values.video_interval,
      grid_size: values.grid_size,
    })

    if (ingestTaskId) {
      await retryTask(ingestTaskId, buildFormData(urls[0], values.platform) as any)
      toast.success('已提交重新生成任务')
      return
    }

    const tasksById = new Map<string, Task>()
    const sourceToTaskId = new Map<string, string>()
    for (const t of tasks) {
      tasksById.set(t.id, t)
      const key = buildSourceKeyFromTask(t)
      if (key && !sourceToTaskId.has(key)) sourceToTaskId.set(key, t.id)
    }

    const nextItems: BatchItem[] = urls.map((url, idx) => ({
      id: `${Date.now()}-${idx}`,
      url,
      platform: '',
      status: 'queued',
    }))
    setBatchItems(nextItems)
    setBatchRunning(true)
    setStopAfterCurrent(false)
    stopAfterCurrentRef.current = false

    let succeeded = 0
    let failed = 0
    let skipped = 0

    for (const item of nextItems) {
      if (stopAfterCurrentRef.current && succeeded + failed + skipped > 0) {
        updateBatchItem(item.id, { status: 'skipped' })
        skipped += 1
        continue
      }

      const itemPlatform =
        values.platform === 'local'
          ? 'local'
          : inferPlatformFromUrl(values.platform, item.url, autoDetectPlatform && values.platform !== 'local')

      updateBatchItem(item.id, { status: 'running', platform: itemPlatform, error: undefined })

      const formData = buildFormData(item.url, itemPlatform)
      const sourceKey = buildSourceKeyFromUrl(itemPlatform, item.url)

      try {
        const existingTaskId = sourceKey ? sourceToTaskId.get(sourceKey) || null : null
        if (existingTaskId) {
          const existingTask = tasksById.get(existingTaskId)
          const title = existingTask?.audioMeta?.title || existingTask?.formData?.video_url || existingTaskId

          if (duplicateStrategy === 'skip') {
            updateBatchItem(item.id, { status: 'skipped', taskId: existingTaskId })
            skipped += 1
            continue
          }

          if (duplicateStrategy === 'ask') {
            const shouldRegenerate = window.confirm(
              `检测到该视频已在库中：\n${title}\n\n是否重新生成并覆盖原任务？\n\n确定：重新生成并覆盖\n取消：跳过该条`
            )
            if (!shouldRegenerate) {
              updateBatchItem(item.id, { status: 'skipped', taskId: existingTaskId })
              skipped += 1
              continue
            }
          }

          await retryTask(existingTaskId, formData as any)
          updateBatchItem(item.id, { status: 'success', taskId: existingTaskId })
          succeeded += 1
          continue
        }

        const resp = await generateNote(formData as any, { silent: true })
        const taskId = resp?.task_id
        if (!taskId) throw new Error('任务创建失败：未返回 task_id')

        addPendingTask(taskId, itemPlatform, formData as any)
        if (sourceKey) sourceToTaskId.set(sourceKey, taskId)

        updateBatchItem(item.id, { status: 'success', taskId })
        succeeded += 1
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        updateBatchItem(item.id, { status: 'failed', error: msg })
        failed += 1
      }
    }

    setBatchRunning(false)
    setStopAfterCurrent(false)

    if (failed === 0 && skipped === 0) toast.success(`批量入库完成：${succeeded}/${nextItems.length}`)
    else toast.success(`批量入库结束：成功${succeeded}，失败${failed}，跳过${skipped}`)
    return

    // message.success('已提交任务')
  }
  const onInvalid = (errors: FieldErrors<NoteFormValues>) => {
    console.warn('表单校验失败：', errors)
    // message.error('请完善所有必填项后再提交')
  }
  const handleCreateNew = () => {
    // 🔁 这里清空当前任务状态
    // 比如调用 resetCurrentTask() 或者 navigate 到一个新页面
    setIngestTask(null)
    setUploadSuccess(false)
    form.reset({
      platform: 'bilibili',
      quality: 'medium',
      video_urls: [''],
      model_name: modelList[0]?.model_name || '',
      style: 'minimal',
      extras: '',
      screenshot: false,
      link: false,
      video_understanding: false,
      video_interval: 4,
      grid_size: [3, 3],
      format: [],
    })
  }
  const FormButton = () => {
    const label = generating ? '正在生成…' : editing ? '重新生成并入库' : '生成笔记并入库'

    return (
      <div className="flex gap-2">
        <Button
          type="submit"
          className={
            editing || (!editing && (batchRunning || batchItems.length > 0))
              ? 'flex-1 bg-primary'
              : 'w-full bg-primary'
          }
          disabled={generating}
        >
          {generating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {batchRunning
            ? '批量处理中…'
            : !editing && cleanedVideoUrls.length > 1
              ? `批量生成并入库（${cleanedVideoUrls.length}）`
              : label}
        </Button>

        {!editing && batchRunning && (
          <Button
            type="button"
            variant="outline"
            className="w-32"
            disabled={stopAfterCurrent}
            onClick={() => setStopAfterCurrent(true)}
          >
            <PauseCircle className="mr-2 h-4 w-4" />
            停止
          </Button>
        )}

        {!editing && !batchRunning && batchItems.length > 0 && (
          <Button type="button" variant="outline" className="w-32" onClick={resetBatch}>
            <X className="mr-2 h-4 w-4" />
            清空
          </Button>
        )}

        {editing && (
          <Button type="button" variant="outline" className="w-1/3" onClick={handleCreateNew}>
            <Plus className="mr-2 h-4 w-4" />
            新建任务
          </Button>
        )}
      </div>
    )
  }

  /* -------------------- 渲染 -------------------- */
  return (
    <div className="h-full w-full">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit, onInvalid)} className="space-y-4">
          {/* 顶部按钮 */}
          <FormButton></FormButton>

          {/* 视频链接 & 平台 */}
          <SectionHeader title="视频链接" tip="支持 B 站、YouTube 等平台" />
          <div className="flex gap-2">
            {/* 平台选择 */}

            <FormField
              control={form.control}
              name="platform"
              render={({ field }) => (
                <FormItem>
                  <Select
                    disabled={!!editing}
                    value={field.value}
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {videoPlatforms?.map(p => (
                        <SelectItem key={p.value} value={p.value}>
                          <div className="flex items-center justify-center gap-2">
                            <div className="h-4 w-4">{p.logo()}</div>
                            <span>{p.label}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage style={{ display: 'none' }} />
                </FormItem>
              )}
            />
            {/* 链接输入 / 上传框 */}
            <FormField
              control={form.control}
              name="video_urls.0"
              render={({ field }) => (
                <FormItem className="flex-1">
                  {platform === 'local' ? (
                    <>
                      <Input disabled={!!editing} placeholder="请输入本地视频路径" {...field} />
                    </>
                  ) : (
                    <Input disabled={!!editing} placeholder="请输入视频网站链接" {...field} />
                  )}
                  <FormMessage style={{ display: 'none' }} />
                </FormItem>
              )}
            />
          </div>

          {platform !== 'local' && (
            <div className="mt-2 space-y-2">
              {videoUrlFields.slice(1).map((row, idx) => {
                const fieldIndex = idx + 1
                return (
                  <div key={row.id} className="flex gap-2">
                    <div className="w-32" />
                    <FormField
                      control={form.control}
                      name={`video_urls.${fieldIndex}`}
                      render={({ field }) => (
                        <FormItem className="flex-1">
                          <Input disabled={!!editing} placeholder="请输入视频网站链接" {...field} />
                          <FormMessage style={{ display: 'none' }} />
                        </FormItem>
                      )}
                    />
                    {!editing && (
                      <Button
                        type="button"
                        variant="outline"
                        className="h-10 w-10 px-0"
                        onClick={() => removeVideoUrl(fieldIndex)}
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                )
              })}

              {!editing && (
                <div className="flex gap-2">
                  <div className="w-32" />
                  <Button type="button" variant="outline" className="flex-1" onClick={() => appendVideoUrl('')}>
                    <Plus className="mr-2 h-4 w-4" />
                    添加
                  </Button>
                </div>
              )}

              {!editing && (
                <div className="grid grid-cols-2 gap-2">
                  <label className="flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
                    <span className="font-medium text-slate-700">自动识别平台（按链接域名）</span>
                    <Checkbox
                      checked={autoDetectPlatform}
                      onCheckedChange={checked => setAutoDetectPlatform(Boolean(checked))}
                    />
                  </label>
                  <div className="space-y-1">
                    <div className="text-xs font-medium text-slate-600">重复处理</div>
                    <Select value={duplicateStrategy} onValueChange={v => setDuplicateStrategy(v as DuplicateStrategy)}>
                      <SelectTrigger className="h-8">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="ask">每次提示</SelectItem>
                        <SelectItem value="skip">跳过已存在</SelectItem>
                        <SelectItem value="regenerate">直接重新生成</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}
            </div>
          )}

          <FormField
            control={form.control}
            name="video_urls.0"
            render={({ field }) => (
              <FormItem className="flex-1">
                {platform === 'local' && (
                  <>
                    <div
                      className="hover:border-primary mt-2 flex h-40 cursor-pointer items-center justify-center rounded-md border-2 border-dashed border-gray-300 transition-colors"
                      onDragOver={e => {
                        e.preventDefault()
                        e.stopPropagation()
                      }}
                      onDrop={e => {
                        e.preventDefault()
                        const file = e.dataTransfer.files?.[0]
                        if (file) handleFileUpload(file, field.onChange)
                      }}
                      onClick={() => {
                        const input = document.createElement('input')
                        input.type = 'file'
                        input.accept = 'video/*'
                        input.onchange = e => {
                          const file = (e.target as HTMLInputElement).files?.[0]
                          if (file) handleFileUpload(file, field.onChange)
                        }
                        input.click()
                      }}
                    >
                      {isUploading ? (
                        <p className="text-center text-sm text-blue-500">上传中，请稍候…</p>
                      ) : uploadSuccess ? (
                        <p className="text-center text-sm text-green-500">上传成功！</p>
                      ) : (
                        <p className="text-center text-sm text-gray-500">
                          拖拽文件到这里上传 <br />
                          <span className="text-xs text-gray-400">或点击选择文件</span>
                        </p>
                      )}
                    </div>
                  </>
                )}
                <FormMessage />
              </FormItem>
            )}
          />
          {batchItems.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold text-slate-800">批量队列</div>
                <div className="flex items-center gap-3 text-xs text-slate-500">
                  <span>
                    {batchItems.filter(i => i.status === 'success').length}/{batchItems.length}
                  </span>
                  {!batchRunning && (
                    <Button type="button" variant="ghost" size="sm" onClick={resetBatch}>
                      清空
                    </Button>
                  )}
                </div>
              </div>

              <ScrollArea className="mt-2 h-40">
                <div className="space-y-2 pr-2">
                  {batchItems.map(item => (
                    <div
                      key={item.id}
                      className="flex items-start gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
                    >
                      <div className="mt-0.5">
                        {item.status === 'running' ? (
                          <Loader2 className="h-4 w-4 animate-spin text-slate-500" />
                        ) : item.status === 'success' ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                        ) : item.status === 'failed' ? (
                          <XCircle className="h-4 w-4 text-rose-600" />
                        ) : item.status === 'skipped' ? (
                          <PauseCircle className="h-4 w-4 text-amber-600" />
                        ) : (
                          <div className="h-4 w-4" />
                        )}
                      </div>

                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="break-all text-xs font-medium text-slate-700">{item.url}</div>
                        {item.platform ? (
                          <div className="text-[10px] text-slate-500">平台：{item.platform}</div>
                        ) : null}
                        {item.error ? <div className="break-all text-xs text-rose-600">{item.error}</div> : null}
                      </div>

                      <div className="shrink-0 text-[10px] text-slate-500">
                        {item.status === 'queued'
                          ? '等待'
                          : item.status === 'running'
                            ? '处理中'
                            : item.status === 'success'
                              ? '完成'
                              : item.status === 'failed'
                                ? '失败'
                                : '已跳过'}
                      </div>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            {/* 模型选择 */}
            {

             modelList.length>0?(     <FormField
               className="w-full"
               control={form.control}
               name="model_name"
               render={({ field }) => (
                 <FormItem>
                   <SectionHeader title="模型选择" tip="不同模型效果不同，建议自行测试" />
                   <Select
                     onOpenChange={()=>{
                       loadEnabledModels()
                     }}
                     value={field.value}
                     onValueChange={field.onChange}
                     defaultValue={field.value}
                   >
                     <FormControl>
                       <SelectTrigger className="w-full min-w-0 truncate">
                         <SelectValue />
                       </SelectTrigger>
                     </FormControl>
                     <SelectContent>
                       {modelList.map(m => (
                         <SelectItem key={m.id} value={m.model_name}>
                           {m.model_name}
                         </SelectItem>
                       ))}
                     </SelectContent>
                   </Select>
                   <FormMessage />
                 </FormItem>
               )}
             />): (
               <FormItem>
                 <SectionHeader title="模型选择" tip="不同模型效果不同，建议自行测试" />
                  <Button type={'button'} variant={
                    'outline'
                  } onClick={()=>{goModelAdd()}}>请先添加模型</Button>
                 <FormMessage />
               </FormItem>
             )
            }

            {/* 笔记风格 */}
            <FormField
              className="w-full"
              control={form.control}
              name="style"
              render={({ field }) => (
                <FormItem>
                  <SectionHeader title="笔记风格" tip="选择生成笔记的呈现风格" />
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger className="w-full min-w-0 truncate">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {noteStyles.map(({ label, value }) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          <div className="grid grid-cols-3 gap-2">
            <Popover
              trigger="click"
              placement="top"
              open={advancedPopover === 'video'}
              onOpenChange={open => setAdvancedPopover(open ? 'video' : null)}
              content={
                <div className="w-[360px] max-w-[calc(100vw-2rem)]">
                  <div className="mb-3 flex items-center justify-between border-b border-slate-200 pb-2">
                    <div className="text-sm font-semibold text-slate-800">视频理解</div>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setAdvancedPopover(null)}>
                      关闭
                    </Button>
                  </div>

                  <div className="flex flex-col gap-3">
                    <FormField
                      control={form.control}
                      name="video_understanding"
                      render={({ field }) => (
                        <FormItem>
                          <div className="flex items-center justify-between gap-2">
                            <FormLabel className="text-sm">启用</FormLabel>
                            <Checkbox
                              checked={videoUnderstandingEnabled}
                              onCheckedChange={v =>
                                form.setValue('video_understanding', Boolean(v), { shouldDirty: true })
                              }
                            />
                          </div>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <div className="grid grid-cols-2 gap-4">
                      <FormField
                        control={form.control}
                        name="video_interval"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>采样间隔（秒）</FormLabel>
                            <Input disabled={!videoUnderstandingEnabled} type="number" {...field} />
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="grid_size"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>拼图尺寸（列 × 行）</FormLabel>
                            <div className="flex items-center space-x-2">
                              <Input
                                disabled={!videoUnderstandingEnabled}
                                type="number"
                                value={field.value?.[0] || 3}
                                onChange={e => field.onChange([+e.target.value, field.value?.[1] || 3])}
                                className="w-16"
                              />
                              <span>x</span>
                              <Input
                                disabled={!videoUnderstandingEnabled}
                                type="number"
                                value={field.value?.[1] || 3}
                                onChange={e => field.onChange([field.value?.[0] || 3, +e.target.value])}
                                className="w-16"
                              />
                            </div>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    <Alert type="warning" showIcon message="提示：视频理解功能需要多模态模型" className="text-sm" />
                  </div>
                </div>
              }
            >
              <Button type="button" variant="outline" size="sm" className="w-full justify-center">
                视频理解
              </Button>
            </Popover>

            <Popover
              trigger="click"
              placement="top"
              open={advancedPopover === 'format'}
              onOpenChange={open => setAdvancedPopover(open ? 'format' : null)}
              content={
                <div className="w-[360px] max-w-[calc(100vw-2rem)]">
                  <div className="mb-3 flex items-center justify-between border-b border-slate-200 pb-2">
                    <div className="text-sm font-semibold text-slate-800">笔记格式</div>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setAdvancedPopover(null)}>
                      关闭
                    </Button>
                  </div>

                  <FormField
                    control={form.control}
                    name="format"
                    render={({ field }) => (
                      <FormItem>
                        <div className="mb-2 text-xs text-slate-500">选择要包含的笔记元素</div>
                        <CheckboxGroup
                          value={field.value}
                          onChange={field.onChange}
                          disabledMap={{
                            link: platform === 'local',
                            screenshot: !videoUnderstandingEnabled,
                          }}
                        />
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              }
            >
              <Button type="button" variant="outline" size="sm" className="w-full justify-center">
                笔记格式
              </Button>
            </Popover>

            <Popover
              trigger="click"
              placement="top"
              open={advancedPopover === 'extras'}
              onOpenChange={open => setAdvancedPopover(open ? 'extras' : null)}
              content={
                <div className="w-[360px] max-w-[calc(100vw-2rem)]">
                  <div className="mb-3 flex items-center justify-between border-b border-slate-200 pb-2">
                    <div className="text-sm font-semibold text-slate-800">备注</div>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setAdvancedPopover(null)}>
                      关闭
                    </Button>
                  </div>

                  <FormField
                    control={form.control}
                    name="extras"
                    render={({ field }) => (
                      <FormItem>
                        <div className="mb-2 text-xs text-slate-500">可在 Prompt 结尾附加自定义说明</div>
                        <Textarea className="min-h-[120px]" placeholder="笔记需要罗列出 xxx 关键点…" {...field} />
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              }
            >
              <Button type="button" variant="outline" size="sm" className="w-full justify-center">
                备注
              </Button>
            </Popover>
          </div>
        </form>
      </Form>
    </div>
  )
}

export default NoteForm
