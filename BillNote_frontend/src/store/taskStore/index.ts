import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { delete_task, generateNote, reingest_dify } from '@/services/note.ts'
import { v4 as uuidv4 } from 'uuid'
import toast from 'react-hot-toast'


export type TaskStatus =
  | 'PENDING'
  | 'PARSING'
  | 'DOWNLOADING'
  | 'TRANSCRIBING'
  | 'SUMMARIZING'
  | 'FORMATTING'
  | 'SAVING'
  | 'SUCCESS'
  | 'FAILED'
  | string

export interface AudioMeta {
  cover_url: string
  duration: number
  file_path: string
  platform: string
  raw_info: any
  title: string
  video_id: string
}

export interface Segment {
  start: number
  end: number
  text: string
}

export interface Transcript {
  full_text: string
  language: string
  raw: any
  segments: Segment[]
}
export interface Markdown {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at: string
}

export interface Task {
  id: string
  platform: string
  markdown: string|Markdown [] //为了兼容之前的笔记
  transcript: Transcript
  status: TaskStatus
  progress?: number
  message?: string
  audioMeta: AudioMeta
  dify?: {
    base_url?: string
    dataset_id?: string
    document_id?: string
    batch?: string
    transcript?: {
      dataset_id?: string
      document_id?: string
      batch?: string
    }
    note?: {
      dataset_id?: string
      document_id?: string
      batch?: string
    }
  }
  dify_indexing?: any
  dify_error?: string
  createdAt: string
  successAt?: string
  formData: {
    video_url: string
    link?: boolean
    screenshot?: boolean
    platform: string
    quality: string
    model_name: string
    provider_id: string
    format?: string[]
    style?: string
    extras?: string
    video_understanding?: boolean
    video_interval?: number
    grid_size?: number[]
  }
}

interface TaskStore {
  tasks: Task[]
  currentTaskId: string | null
  ingestTaskId: string | null
  addPendingTask: (taskId: string, platform: string, formData: Task['formData']) => void
  updateTaskContent: (id: string, data: Partial<Omit<Task, 'id' | 'createdAt'>>) => void
  upsertTaskFromBackend: (id: string, payload: any) => void
  removeTask: (id: string) => Promise<void>
  clearTasks: () => void
  setCurrentTask: (taskId: string | null) => void
  setIngestTask: (taskId: string | null) => void
  getCurrentTask: () => Task | null
  getIngestTask: () => Task | null
  retryTask: (id: string, payload?: Task['formData']) => Promise<void>
  reingestTask: (id: string) => Promise<void>
}

export const useTaskStore = create<TaskStore>()(
  persist(
    (set, get) => ({
      tasks: [],
      currentTaskId: null,
      ingestTaskId: null,

      addPendingTask: (taskId: string, platform: string, formData: any) =>

        set(state => ({
          tasks: [
            {
              formData: formData,
              id: taskId,
              status: 'PENDING',
              progress: 0,
              markdown: '',
              platform: platform,
              transcript: {
                full_text: '',
                language: '',
                raw: null,
                segments: [],
              },
              createdAt: new Date().toISOString(),
              audioMeta: {
                cover_url: '',
                duration: 0,
                file_path: '',
                platform: '',
                raw_info: null,
                title: '',
                video_id: '',
              },
            },
            ...state.tasks,
          ],
          currentTaskId: taskId, // 默认设置为当前任务
        })),

      updateTaskContent: (id, data) =>
          set(state => ({
            tasks: state.tasks.map(task => {
              if (task.id !== id) return task

              const shouldStampSuccessAt =
                typeof (data as any)?.status === 'string' &&
                (data as any).status === 'SUCCESS' &&
                task.status !== 'SUCCESS' &&
                !task.successAt
              const successAtPatch = shouldStampSuccessAt ? { successAt: new Date().toISOString() } : {}

              // 如果是 markdown 字符串，封装为版本
              if (typeof data.markdown === 'string') {
                const prev = task.markdown
                const newVersion: Markdown = {
                  ver_id: `${task.id}-${uuidv4()}`,
                  content: data.markdown,
                  style: task.formData.style || '',
                  model_name: task.formData.model_name || '',
                  created_at: new Date().toISOString(),
                }

                let updatedMarkdown: Markdown[]
                if (Array.isArray(prev)) {
                  updatedMarkdown = [newVersion, ...prev]
                } else {
                  updatedMarkdown = [
                    newVersion,
                    ...(typeof prev === 'string' && prev
                        ? [{
                          ver_id: `${task.id}-${uuidv4()}`,
                          content: prev,
                          style: task.formData.style || '',
                          model_name: task.formData.model_name || '',
                          created_at: new Date().toISOString(),
                        }]
                        : []),
                  ]
                }

                return {
                  ...task,
                  ...data,
                  ...successAtPatch,
                  markdown: updatedMarkdown,
                }
              }

              return { ...task, ...data, ...successAtPatch }
            }),
          })),

      upsertTaskFromBackend: (id: string, payload: any) =>
        set(state => {
          const taskId = String(id || '').trim()
          if (!taskId) return state

          const existing = state.tasks.find(t => t.id === taskId) || null
          const res = (payload && typeof payload === 'object' ? payload : {}) as any
          const status = String(res.status || existing?.status || 'SUCCESS')
          const progress =
            typeof res.progress === 'number' && Number.isFinite(res.progress)
              ? Math.max(0, Math.min(100, Math.round(res.progress)))
              : existing?.progress ?? 100

          const result = (res.result && typeof res.result === 'object' ? res.result : {}) as any
          const audio = (result.audio_meta && typeof result.audio_meta === 'object' ? result.audio_meta : {}) as any
          const transcript = (result.transcript && typeof result.transcript === 'object' ? result.transcript : null) as any
          const markdown = typeof result.markdown === 'string' ? result.markdown : existing?.markdown ?? ''

          const audioMeta: AudioMeta = {
            cover_url: String(audio.cover_url || existing?.audioMeta?.cover_url || ''),
            duration: Number(audio.duration || existing?.audioMeta?.duration || 0),
            file_path: String(audio.file_path || existing?.audioMeta?.file_path || ''),
            platform: String(audio.platform || existing?.audioMeta?.platform || existing?.platform || ''),
            raw_info: (audio.raw_info ?? existing?.audioMeta?.raw_info ?? null) as any,
            title: String(audio.title || existing?.audioMeta?.title || ''),
            video_id: String(audio.video_id || existing?.audioMeta?.video_id || ''),
          }

          const defaultFormData: Task['formData'] = existing?.formData || {
            video_url: '',
            platform: audioMeta.platform || '',
            quality: '',
            model_name: '',
            provider_id: '',
            format: [],
            style: '',
            grid_size: [],
          }

          const requestMeta =
            (res.request && typeof res.request === 'object' ? res.request : null) ||
            (result.request && typeof result.request === 'object' ? result.request : null) ||
            null

          const mergedFormData: Task['formData'] = {
            ...defaultFormData,
            video_url: typeof requestMeta?.video_url === 'string' ? requestMeta.video_url : defaultFormData.video_url,
            platform:
              typeof requestMeta?.platform === 'string'
                ? requestMeta.platform
                : defaultFormData.platform || audioMeta.platform || '',
            quality: typeof requestMeta?.quality === 'string' ? requestMeta.quality : defaultFormData.quality,
            model_name:
              typeof requestMeta?.model_name === 'string' ? requestMeta.model_name : defaultFormData.model_name,
            provider_id:
              typeof requestMeta?.provider_id === 'string' ? requestMeta.provider_id : defaultFormData.provider_id,
            format: Array.isArray(requestMeta?.format) ? requestMeta.format : defaultFormData.format,
            style: typeof requestMeta?.style === 'string' ? requestMeta.style : defaultFormData.style,
            extras: typeof requestMeta?.extras === 'string' ? requestMeta.extras : defaultFormData.extras,
            link: typeof requestMeta?.link === 'boolean' ? requestMeta.link : defaultFormData.link,
            screenshot:
              typeof requestMeta?.screenshot === 'boolean' ? requestMeta.screenshot : defaultFormData.screenshot,
            video_understanding:
              typeof requestMeta?.video_understanding === 'boolean'
                ? requestMeta.video_understanding
                : defaultFormData.video_understanding,
            video_interval:
              typeof requestMeta?.video_interval === 'number' ? requestMeta.video_interval : defaultFormData.video_interval,
            grid_size: Array.isArray(requestMeta?.grid_size) ? requestMeta.grid_size : defaultFormData.grid_size,
          }

          const patch: Partial<Task> = {
            status,
            progress,
            message: String(res.message || existing?.message || ''),
            dify: (res.dify ?? existing?.dify) as any,
            dify_indexing: (res.dify_indexing ?? existing?.dify_indexing) as any,
            dify_error: (res.dify_error ?? existing?.dify_error) as any,
            markdown,
            transcript: (transcript ?? existing?.transcript) as any,
            audioMeta,
            platform: audioMeta.platform || existing?.platform || '',
            formData: mergedFormData,
          }

          if (existing) {
            return {
              ...state,
              tasks: state.tasks.map(t => (t.id === taskId ? { ...t, ...patch } : t)),
            }
          }

          const newTask: Task = {
            id: taskId,
            platform: patch.platform || '',
            markdown: markdown,
            transcript: (patch.transcript ?? {
              full_text: '',
              language: '',
              raw: null,
              segments: [],
            }) as any,
            status,
            progress,
            message: patch.message,
            audioMeta,
            dify: patch.dify,
            dify_indexing: patch.dify_indexing,
            dify_error: patch.dify_error,
            createdAt: new Date().toISOString(),
            formData: mergedFormData,
          }

          return { ...state, tasks: [newTask, ...state.tasks] }
        }),


      getCurrentTask: () => {
        const currentTaskId = get().currentTaskId
        return get().tasks.find(task => task.id === currentTaskId) || null
      },
      getIngestTask: () => {
        const ingestTaskId = get().ingestTaskId
        return get().tasks.find(task => task.id === ingestTaskId) || null
      },
      retryTask: async (id: string, payload?: any) => {

        if (!id){
          toast.error('任务不存在')
          return
        }
        const task = get().tasks.find(task => task.id === id)
        console.log('retry',task)
        if (!task) return

        const newFormData = payload || task.formData
        await generateNote({
          ...newFormData,
          task_id: id,
        })

        set(state => ({
          tasks: state.tasks.map(t =>
              t.id === id
                  ? {
                    ...t,
                    formData: newFormData, // ✅ 显式更新 formData
                    status: 'PENDING',
                  }
                  : t
          ),
        }))
      },

      reingestTask: async (id: string) => {
        if (!id) {
          toast.error('任务不存在')
          return
        }

        const task = get().tasks.find(task => task.id === id)
        if (!task) {
          toast.error('任务不存在')
          return
        }

        try {
          // 清空入库错误，让轮询继续推进索引状态
          set(state => ({
            tasks: state.tasks.map(t =>
              t.id === id
                ? {
                    ...t,
                    status: 'SUCCESS',
                    progress: 100,
                    dify_error: undefined,
                    dify_indexing: undefined,
                  }
                : t
            ),
          }))

          const res: any = await reingest_dify(
            {
              task_id: id,
              video_url: task.formData?.video_url,
              platform: task.platform || task.formData?.platform,
              include_transcript: true,
              include_note: true,
            },
            { silent: true }
          )

          const difyError = res?.dify_error ? String(res.dify_error) : undefined
          if (difyError) {
            toast.error('重新入库失败：' + difyError)
          } else {
            toast.success('已重新提交入库')
          }

          set(state => ({
            tasks: state.tasks.map(t =>
              t.id === id
                ? {
                    ...t,
                    dify: res?.dify ?? t.dify,
                    dify_error: difyError,
                    dify_indexing: undefined,
                  }
                : t
            ),
          }))
        } catch (e) {
          toast.error('重新入库失败')
          throw e
        }
      },

      removeTask: async id => {
        const task = get().tasks.find(t => t.id === id)

        // 更新 Zustand 状态
        set(state => ({
          tasks: state.tasks.filter(task => task.id !== id),
          currentTaskId: state.currentTaskId === id ? null : state.currentTaskId,
          ingestTaskId: state.ingestTaskId === id ? null : state.ingestTaskId,
        }))

        // Always call backend cleanup: the local files may exist even if the task is not in localStorage.
        await delete_task({
          task_id: id,
          video_id: task?.audioMeta?.video_id || '',
          platform: task?.platform || '',
        })
      },

      clearTasks: () => set({ tasks: [], currentTaskId: null, ingestTaskId: null }),

      setCurrentTask: taskId => set({ currentTaskId: taskId }),
      setIngestTask: taskId => set({ ingestTaskId: taskId }),
    }),
    {
      name: 'task-storage',
      // Persist tasks only; don't persist which task is "currently selected" so the app opens in "new task" mode.
      partialize: state => ({ tasks: state.tasks }),
      // Backward-compatible: older localStorage may still contain currentTaskId; always clear it on hydrate.
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...(persistedState as any),
        currentTaskId: null,
        ingestTaskId: null,
      }),
    }
  )
)
