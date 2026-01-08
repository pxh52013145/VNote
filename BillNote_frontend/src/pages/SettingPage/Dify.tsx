import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  FormDescription,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

import {
  activateDifyProfile,
  activateDifyAppScheme,
  deleteDifyProfile,
  deleteDifyAppScheme,
  getDifyAppSchemes,
  getDifyConfig,
  getDifyProfiles,
  upsertDifyAppScheme,
  upsertDifyProfile,
  updateDifyConfig,
  type DifyConfigUpdateRequest,
  type DifyAppSchemeSummary,
  type DifyProfileSummary,
} from '@/services/dify'
import { useSyncStore } from '@/store/syncStore'

const DifySchema = z.object({
  baseUrl: z
    .string()
    .trim()
    .min(1, '请填写 Dify Base URL')
    .refine(v => {
      try {
        const u = new URL(v)
        return u.protocol === 'http:' || u.protocol === 'https:'
      } catch {
        return false
      }
    }, 'Base URL 必须是合法的 http/https URL'),
  datasetId: z.string().trim().min(1, '请填写 Dataset ID（UUID）'),
  transcriptDatasetId: z.string().trim().optional(),
  noteDatasetId: z.string().trim().optional(),
  serviceApiKey: z.string().trim().optional(),
  appApiKey: z.string().trim().optional(),
  appUser: z.string().trim().min(1, '请填写 user（任意稳定字符串即可）'),
  indexingTechnique: z.enum(['high_quality', 'economy']),
  timeoutSeconds: z.coerce.number().min(5, '超时至少 5 秒').max(600, '超时不建议超过 600 秒'),
})

type DifyFormValues = z.infer<typeof DifySchema>

const DifySetting = () => {
  const [loading, setLoading] = useState(true)
  const [serviceKeyHint, setServiceKeyHint] = useState<string>('')
  const [appKeyHint, setAppKeyHint] = useState<string>('')
  const [configPath, setConfigPath] = useState<string>('')
  const [profiles, setProfiles] = useState<DifyProfileSummary[]>([])
  const [activeProfile, setActiveProfile] = useState<string>('default')
  const [profileBusy, setProfileBusy] = useState(false)
  const [saveAsOpen, setSaveAsOpen] = useState(false)
  const [saveAsName, setSaveAsName] = useState('')

  const [appSchemes, setAppSchemes] = useState<DifyAppSchemeSummary[]>([])
  const [activeAppScheme, setActiveAppScheme] = useState<string>('default')
  const [schemeBusy, setSchemeBusy] = useState(false)
  const [schemeCreateOpen, setSchemeCreateOpen] = useState(false)
  const [schemeCreateName, setSchemeCreateName] = useState('')

  const form = useForm<DifyFormValues>({
    resolver: zodResolver(DifySchema),
    defaultValues: {
      baseUrl: 'http://localhost',
      datasetId: '',
      transcriptDatasetId: '',
      noteDatasetId: '',
      serviceApiKey: '',
      appApiKey: '',
      appUser: 'bilinote',
      indexingTechnique: 'high_quality',
      timeoutSeconds: 60,
    },
  })

  const applyConfigToForm = (cfg: Awaited<ReturnType<typeof getDifyConfig>>) => {
    form.reset({
      baseUrl: cfg.base_url || 'http://localhost',
      datasetId: cfg.dataset_id || '',
      transcriptDatasetId: cfg.transcript_dataset_id || '',
      noteDatasetId: cfg.note_dataset_id || '',
      serviceApiKey: '',
      appApiKey: '',
      appUser: cfg.app_user || 'bilinote',
      indexingTechnique: cfg.indexing_technique === 'economy' ? 'economy' : 'high_quality',
      timeoutSeconds: cfg.timeout_seconds || 60,
    })

    setServiceKeyHint(cfg.service_api_key_set ? `已设置：${cfg.service_api_key_masked || ''}` : '未设置')
    const schemeName = cfg.active_app_scheme ? String(cfg.active_app_scheme) : activeAppScheme
    setActiveAppScheme(schemeName || 'default')
    setAppKeyHint(cfg.app_api_key_set ? `已设置（App 方案：${schemeName || 'default'}）：${cfg.app_api_key_masked || ''}` : `未设置（App 方案：${schemeName || 'default'}）`)
    setConfigPath(cfg.config_path || '')
  }

  useEffect(() => {
    const load = async () => {
      try {
        const [cfg, profilePayload, schemePayload] = await Promise.all([getDifyConfig(), getDifyProfiles(), getDifyAppSchemes()])
        applyConfigToForm(cfg)
        setProfiles(profilePayload.profiles || [])
        setActiveProfile(profilePayload.active_profile || 'default')
        setAppSchemes(schemePayload.schemes || [])
        setActiveAppScheme(schemePayload.active_app_scheme || cfg.active_app_scheme || 'default')
      } catch (e: unknown) {
        toast.error('读取 Dify 配置失败')
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [form])

  const buildPatchFromForm = (values: DifyFormValues): DifyConfigUpdateRequest => {
    const patch: DifyConfigUpdateRequest = {
      base_url: values.baseUrl,
      dataset_id: values.datasetId,
      transcript_dataset_id: values.transcriptDatasetId || '',
      note_dataset_id: values.noteDatasetId || '',
      app_user: values.appUser,
      indexing_technique: values.indexingTechnique,
      timeout_seconds: values.timeoutSeconds,
    }
    if (values.serviceApiKey && values.serviceApiKey.trim()) {
      patch.service_api_key = values.serviceApiKey.trim()
    }
    if (values.appApiKey && values.appApiKey.trim()) {
      patch.app_api_key = values.appApiKey.trim()
    }
    return patch
  }

  const onSubmit = async (values: DifyFormValues) => {
    try {
      const patch = buildPatchFromForm(values)

      const prevProfile = activeProfile
      const prevScheme = activeAppScheme
      const updated = await updateDifyConfig(patch)
      applyConfigToForm(updated)
      try {
        const [profilePayload, schemePayload] = await Promise.all([getDifyProfiles(), getDifyAppSchemes()])
        setProfiles(profilePayload.profiles || [])
        const nextProfile = profilePayload.active_profile || updated.active_profile || prevProfile
        setActiveProfile(nextProfile)

        setAppSchemes(schemePayload.schemes || [])
        const nextScheme = schemePayload.active_app_scheme || updated.active_app_scheme || prevScheme || 'default'
        setActiveAppScheme(nextScheme)

        if (prevProfile === 'default' && nextProfile !== 'default') {
          toast.success(`已自动生成方案：${nextProfile}`)
        } else {
          toast.success('Dify 配置已保存（立即生效）')
        }
        if (nextProfile !== prevProfile || nextScheme !== prevScheme) {
          window.dispatchEvent(new CustomEvent('rag-context-changed'))
        }
      } catch {
        // Ignore scheme refresh errors; config save already succeeded.
        toast.success('Dify 配置已保存（立即生效）')
      }
      useSyncStore.getState().scan({ silent: true })
    } catch (e: unknown) {
      toast.error('保存失败，请检查 Dify 地址/Key 是否正确')
      console.error(e)
    }
  }

  const onActivateProfile = async (name: string) => {
    if (!name || name === activeProfile) return

    if (form.formState.isDirty) {
      const ok = window.confirm('当前表单未保存，切换方案会丢失修改，确定继续？')
      if (!ok) return
    }

    setProfileBusy(true)
    try {
      const nextProfiles = await activateDifyProfile(name)
      setProfiles(nextProfiles.profiles || [])
      setActiveProfile(nextProfiles.active_profile || name)
      const cfg = await getDifyConfig()
      applyConfigToForm(cfg)
      const schemePayload = await getDifyAppSchemes()
      setAppSchemes(schemePayload.schemes || [])
      setActiveAppScheme(schemePayload.active_app_scheme || cfg.active_app_scheme || 'default')
      useSyncStore.getState().scan({ silent: true })
      window.dispatchEvent(new CustomEvent('rag-context-changed'))
      toast.success(`已切换到方案：${name}`)
    } catch (e: unknown) {
      toast.error('切换方案失败')
      console.error(e)
    } finally {
      setProfileBusy(false)
    }
  }

  const onActivateAppScheme = async (name: string) => {
    if (!name || name === activeAppScheme) return

    if (form.formState.isDirty) {
      const ok = window.confirm('当前表单未保存，切换 App 方案会丢失修改，确定继续？')
      if (!ok) return
    }

    setSchemeBusy(true)
    try {
      const schemePayload = await activateDifyAppScheme(name)
      setAppSchemes(schemePayload.schemes || [])
      setActiveAppScheme(schemePayload.active_app_scheme || name)
      const cfg = await getDifyConfig()
      applyConfigToForm(cfg)
      window.dispatchEvent(new CustomEvent('rag-context-changed'))
      toast.success(`已切换 App 方案：${name}`)
    } catch (e: unknown) {
      toast.error('切换 App 方案失败')
      console.error(e)
    } finally {
      setSchemeBusy(false)
    }
  }

  const onCreateAppScheme = async () => {
    const name = schemeCreateName.trim()
    if (!name) {
      toast.error('请输入 App 方案名称')
      return
    }

    setSchemeBusy(true)
    try {
      const schemePayload = await upsertDifyAppScheme({ name, activate: true })
      setAppSchemes(schemePayload.schemes || [])
      setActiveAppScheme(schemePayload.active_app_scheme || name)
      const cfg = await getDifyConfig()
      applyConfigToForm(cfg)
      setSchemeCreateOpen(false)
      setSchemeCreateName('')
      window.dispatchEvent(new CustomEvent('rag-context-changed'))
      toast.success(`已创建并启用 App 方案：${name}`)
    } catch (e: unknown) {
      toast.error('创建 App 方案失败')
      console.error(e)
    } finally {
      setSchemeBusy(false)
    }
  }

  const onSaveAsProfile = async () => {
    const name = saveAsName.trim()
    if (!name) {
      toast.error('请输入方案名称')
      return
    }

    setProfileBusy(true)
    try {
      const values = form.getValues()
      const patch = buildPatchFromForm(values)

      const nextProfiles = await upsertDifyProfile({
        name,
        clone_from: activeProfile,
        activate: true,
        ...patch,
      })
      setProfiles(nextProfiles.profiles || [])
      setActiveProfile(nextProfiles.active_profile || name)

      const cfg = await getDifyConfig()
      applyConfigToForm(cfg)
      const schemePayload = await getDifyAppSchemes()
      setAppSchemes(schemePayload.schemes || [])
      setActiveAppScheme(schemePayload.active_app_scheme || cfg.active_app_scheme || 'default')
      useSyncStore.getState().scan({ silent: true })

      setSaveAsOpen(false)
      setSaveAsName('')
      window.dispatchEvent(new CustomEvent('rag-context-changed'))
      toast.success(`已保存并启用方案：${name}`)
    } catch (e: unknown) {
      toast.error('保存方案失败')
      console.error(e)
    } finally {
      setProfileBusy(false)
    }
  }

  const onDeleteActiveProfile = async () => {
    if (!activeProfile) return
    if ((profiles?.length || 0) <= 1) {
      toast.error('至少保留一个方案')
      return
    }

    const ok = window.confirm(`确定删除方案「${activeProfile}」？`)
    if (!ok) return

    setProfileBusy(true)
    try {
      const nextProfiles = await deleteDifyProfile(activeProfile)
      setProfiles(nextProfiles.profiles || [])
      setActiveProfile(nextProfiles.active_profile || 'default')

      const cfg = await getDifyConfig()
      applyConfigToForm(cfg)
      const schemePayload = await getDifyAppSchemes()
      setAppSchemes(schemePayload.schemes || [])
      setActiveAppScheme(schemePayload.active_app_scheme || cfg.active_app_scheme || 'default')
      useSyncStore.getState().scan({ silent: true })

      window.dispatchEvent(new CustomEvent('rag-context-changed'))
      toast.success('方案已删除')
    } catch (e: unknown) {
      toast.error('删除方案失败')
      console.error(e)
    } finally {
      setProfileBusy(false)
    }
  }

  const onDeleteActiveAppScheme = async () => {
    if (!activeAppScheme) return
    if ((appSchemes?.length || 0) <= 1) {
      toast.error('至少保留一个 App 方案')
      return
    }

    const ok = window.confirm(`确定删除 App 方案「${activeAppScheme}」？`)
    if (!ok) return

    setSchemeBusy(true)
    try {
      const schemePayload = await deleteDifyAppScheme(activeAppScheme)
      setAppSchemes(schemePayload.schemes || [])
      setActiveAppScheme(schemePayload.active_app_scheme || 'default')
      const cfg = await getDifyConfig()
      applyConfigToForm(cfg)
      window.dispatchEvent(new CustomEvent('rag-context-changed'))
      toast.success('App 方案已删除')
    } catch (e: unknown) {
      toast.error('删除 App 方案失败')
      console.error(e)
    } finally {
      setSchemeBusy(false)
    }
  }

  if (loading) {
    return <div className="p-6 text-sm text-slate-600">加载中…</div>
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-2xl">
        <div className="text-xl font-semibold text-slate-900">Dify / RAG 配置</div>
        <div className="mt-1 text-sm text-slate-600">
          在这里配置 Dify 的 Dataset 与 API Key，保存后后端会自动用于“入库/对话”，无需修改 `.env`。
        </div>

        <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-5">
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                  <div className="flex-1">
                    <div className="text-sm font-medium text-slate-900">配置方案</div>
                    <div className="mt-2">
                      <Select value={activeProfile} onValueChange={onActivateProfile} disabled={profileBusy}>
                        <SelectTrigger className="w-full md:w-[280px]">
                          <SelectValue placeholder="选择方案" />
                        </SelectTrigger>
                        <SelectContent>
                          {profiles.map(p => (
                            <SelectItem key={p.name} value={p.name}>
                              {p.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">用于在本地/服务器 Dify 配置之间快速切换。</div>
                  </div>

                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => setSaveAsOpen(true)}
                      disabled={profileBusy}
                    >
                      另存为方案
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      onClick={onDeleteActiveProfile}
                      disabled={profileBusy || (profiles?.length || 0) <= 1}
                    >
                      删除方案
                    </Button>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                  <div className="flex-1">
                    <div className="text-sm font-medium text-slate-900">RAG App 方案</div>
                    <div className="mt-2">
                      <Select value={activeAppScheme} onValueChange={onActivateAppScheme} disabled={schemeBusy || profileBusy}>
                        <SelectTrigger className="w-full md:w-[280px]">
                          <SelectValue placeholder="选择 App 方案" />
                        </SelectTrigger>
                        <SelectContent>
                          {appSchemes.map(s => (
                            <SelectItem key={s.name} value={s.name}>
                              {s.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">
                      用于切换不同 Dify App API Key（不同 RAG 模式）；不会影响本地/MinIO 同步与对账。
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <Button type="button" variant="secondary" onClick={() => setSchemeCreateOpen(true)} disabled={schemeBusy || profileBusy}>
                      新增 App 方案
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      onClick={onDeleteActiveAppScheme}
                      disabled={schemeBusy || profileBusy || (appSchemes?.length || 0) <= 1}
                    >
                      删除 App 方案
                    </Button>
                  </div>
                </div>
              </div>

              <FormField
                control={form.control}
                name="baseUrl"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Dify Base URL</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="http://localhost" />
                    </FormControl>
                    <FormDescription>例如 `http://localhost` 或公网地址。</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="datasetId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Dataset ID</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="e78d6848-ff8c-46f8-91f9-836d4bdbc2fd" />
                    </FormControl>
                    <FormDescription>
                      可直接粘贴 `datasets/xxxx-...`，后端会自动提取 UUID。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
                <FormField
                  control={form.control}
                  name="transcriptDatasetId"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Transcript Dataset ID（可选）</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="6de4f7f8-50a6-4e94-aaeb-e5676e543e4a" />
                      </FormControl>
                      <FormDescription>留空则使用上方 Dataset ID</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="noteDatasetId"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Note Dataset ID（可选）</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="c6bb1a44-df05-4447-97d1-a782fba93455" />
                      </FormControl>
                      <FormDescription>留空则使用上方 Dataset ID</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
                <FormField
                  control={form.control}
                  name="serviceApiKey"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Service API Key（写入知识库）</FormLabel>
                      <FormControl>
                        <Input {...field} type="password" placeholder="dataset-..." />
                      </FormControl>
                      <FormDescription>{serviceKeyHint}</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="appApiKey"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>App API Key（RAG 对话）</FormLabel>
                      <FormControl>
                        <Input {...field} type="password" placeholder="app-..." />
                      </FormControl>
                      <FormDescription>{appKeyHint}</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
                <FormField
                  control={form.control}
                  name="indexingTechnique"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Indexing</FormLabel>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder="选择" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value="high_quality">high_quality</SelectItem>
                          <SelectItem value="economy">economy</SelectItem>
                        </SelectContent>
                      </Select>
                      <FormDescription>与 Dify 数据集索引设置保持一致。</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="appUser"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>User</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="bilinote" />
                      </FormControl>
                      <FormDescription>Dify chat API 必填；任意稳定字符串即可。</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="timeoutSeconds"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Timeout（秒）</FormLabel>
                      <FormControl>
                        <Input {...field} type="number" min={5} max={600} step={1} />
                      </FormControl>
                      <FormDescription>建议 60–120 秒。</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              {configPath && (
                <div className="text-xs text-slate-500">
                  配置文件：<span className="font-mono">{configPath}</span>
                </div>
              )}

              <div className="flex items-center gap-3 pt-2">
                <Button type="submit">保存</Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    form.reset()
                    toast.success('已重置表单（未保存）')
                  }}
                >
                  重置
                </Button>
              </div>
            </form>
          </Form>
        </div>

        <Dialog
          open={saveAsOpen}
          onOpenChange={open => {
            if (!profileBusy) setSaveAsOpen(open)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>另存为配置方案</DialogTitle>
              <DialogDescription>
                新方案会基于当前方案复制（包含已保存的 Key），并用当前表单内容覆盖，然后自动启用。
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-2">
              <div className="text-sm font-medium text-slate-900">方案名称</div>
              <Input
                value={saveAsName}
                onChange={e => setSaveAsName(e.target.value)}
                placeholder="例如：本地 / 服务器"
                disabled={profileBusy}
              />
            </div>

            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setSaveAsOpen(false)} disabled={profileBusy}>
                取消
              </Button>
              <Button type="button" onClick={onSaveAsProfile} disabled={profileBusy}>
                保存并启用
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog
          open={schemeCreateOpen}
          onOpenChange={open => {
            if (!schemeBusy) setSchemeCreateOpen(open)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>新增 App 方案</DialogTitle>
              <DialogDescription>同一个 Dify Profile 下可配置多个 App API Key，用于切换不同 RAG 模式。</DialogDescription>
            </DialogHeader>

            <div className="space-y-2">
              <div className="text-sm font-medium text-slate-900">App 方案名称</div>
              <Input
                value={schemeCreateName}
                onChange={e => setSchemeCreateName(e.target.value)}
                placeholder="例如：默认RAG / 多路召回 / 结构化总结"
                disabled={schemeBusy}
              />
            </div>

            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setSchemeCreateOpen(false)} disabled={schemeBusy}>
                取消
              </Button>
              <Button type="button" onClick={onCreateAppScheme} disabled={schemeBusy}>
                创建并启用
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  )
}

export default DifySetting
