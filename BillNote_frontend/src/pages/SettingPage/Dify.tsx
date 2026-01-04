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

import { getDifyConfig, updateDifyConfig, type DifyConfigUpdateRequest } from '@/services/dify'

const DifySchema = z.object({
  baseUrl: z
    .string()
    .trim()
    .min(1, '请填写 Dify Base URL（不要带 /v1）')
    .refine(v => {
      try {
        const u = new URL(v)
        return u.protocol === 'http:' || u.protocol === 'https:'
      } catch {
        return false
      }
    }, 'Base URL 必须是合法的 http/https URL'),
  datasetId: z.string().trim().min(1, '请填写 Dataset ID（UUID）'),
  serviceApiKey: z.string().trim().optional(),
  appApiKey: z.string().trim().optional(),
  appUser: z.string().trim().min(1, '请填写 user（任意稳定字符串即可）'),
  indexingTechnique: z.enum(['high_quality', 'economy']),
  timeoutSeconds: z
    .coerce
    .number()
    .min(5, '超时至少 5 秒')
    .max(600, '超时不建议超过 600 秒'),
})

type DifyFormValues = z.infer<typeof DifySchema>

const DifySetting = () => {
  const [loading, setLoading] = useState(true)
  const [serviceKeyHint, setServiceKeyHint] = useState<string>('')
  const [appKeyHint, setAppKeyHint] = useState<string>('')
  const [configPath, setConfigPath] = useState<string>('')

  const form = useForm<DifyFormValues>({
    resolver: zodResolver(DifySchema),
    defaultValues: {
      baseUrl: 'http://localhost',
      datasetId: '',
      serviceApiKey: '',
      appApiKey: '',
      appUser: 'bilinote',
      indexingTechnique: 'high_quality',
      timeoutSeconds: 60,
    },
  })

  useEffect(() => {
    const load = async () => {
      try {
        const cfg = await getDifyConfig()
        form.reset({
          baseUrl: cfg.base_url || 'http://localhost',
          datasetId: cfg.dataset_id || '',
          serviceApiKey: '',
          appApiKey: '',
          appUser: cfg.app_user || 'bilinote',
          indexingTechnique: cfg.indexing_technique === 'economy' ? 'economy' : 'high_quality',
          timeoutSeconds: cfg.timeout_seconds || 60,
        })
        setServiceKeyHint(cfg.service_api_key_set ? `已设置：${cfg.service_api_key_masked || ''}` : '未设置')
        setAppKeyHint(cfg.app_api_key_set ? `已设置：${cfg.app_api_key_masked || ''}` : '未设置')
        setConfigPath(cfg.config_path || '')
      } catch (e: unknown) {
        toast.error('读取 Dify 配置失败')
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [form])

  const onSubmit = async (values: DifyFormValues) => {
    try {
      const patch: DifyConfigUpdateRequest = {
        base_url: values.baseUrl,
        dataset_id: values.datasetId,
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

      const updated = await updateDifyConfig(patch)
      toast.success('Dify 配置已保存（立即生效）')

      setServiceKeyHint(updated.service_api_key_set ? `已设置：${updated.service_api_key_masked || ''}` : '未设置')
      setAppKeyHint(updated.app_api_key_set ? `已设置：${updated.app_api_key_masked || ''}` : '未设置')
      setConfigPath(updated.config_path || '')

      form.setValue('serviceApiKey', '')
      form.setValue('appApiKey', '')
    } catch (e: unknown) {
      toast.error('保存失败，请检查 Dify 地址/Key 是否正确')
      console.error(e)
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
              <FormField
                control={form.control}
                name="baseUrl"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Dify Base URL</FormLabel>
                    <FormControl>
                      <Input {...field} placeholder="http://localhost" />
                    </FormControl>
                    <FormDescription>不要带 `/v1`，例如 `http://localhost` 或公网地址。</FormDescription>
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
                    <FormDescription>可直接粘贴 `datasets/xxxx-...`，后端会自动提取 UUID。</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

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
      </div>
    </div>
  )
}

export default DifySetting
