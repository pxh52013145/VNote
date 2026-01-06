import { useEffect, useRef } from 'react'
import { useTaskStore } from '@/store/taskStore'
import { get_task_status } from '@/services/note.ts'
import toast from 'react-hot-toast'

export const useTaskPolling = (interval = 3000) => {
  const tasks = useTaskStore(state => state.tasks)
  const updateTaskContent = useTaskStore(state => state.updateTaskContent)

  const tasksRef = useRef(tasks)

  // 每次 tasks 更新，把最新的 tasks 同步进去
  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    const timer = setInterval(async () => {
      const isDifyIndexingCompleted = (payload: any) => {
        const docs = payload?.data
        if (!Array.isArray(docs) || docs.length === 0) return false
        return docs.every(d => typeof d === 'object' && d && d.indexing_status === 'completed')
      }

      const getDifyIndexingError = (payload: any) => {
        const docs = payload?.data
        if (!Array.isArray(docs) || docs.length === 0) return null
        for (const d of docs) {
          if (!d || typeof d !== 'object') continue
          const status = String((d as any).indexing_status || '').toLowerCase()
          if (status !== 'error' && status !== 'failed') continue
          const err = (d as any).error || (d as any).message || null
          return err ? String(err) : `indexing_status=${status}`
        }
        return null
      }

      const pendingTasks = tasksRef.current.filter(task => {
        if (task.status === 'FAILED' || task.status === 'CANCELLED') return false
        if (task.status !== 'SUCCESS') return true
        if (task.dify_error) return false
        if (!task.dify?.batch) {
          // Grace period: note may be done while Dify upload is still in-flight.
          const ageMs = Date.now() - new Date(task.createdAt).getTime()
          return ageMs < 2 * 60 * 1000
        }
        if (getDifyIndexingError(task.dify_indexing)) return false
        return !isDifyIndexingCompleted(task.dify_indexing)
      })

      for (const task of pendingTasks) {
        try {
          console.log('🔄 正在轮询任务：', task.id)
          const res = await get_task_status(task.id)
          const status = res?.status
          if (!status) continue

          const patch = {
            status,
            progress: res?.progress,
            message: res?.message,
            dify: res.dify,
            dify_indexing: res.dify_indexing,
            dify_error: res.dify_error || getDifyIndexingError(res.dify_indexing),
          }

          if (status === 'SUCCESS' && task.status !== 'SUCCESS') {
            const { markdown, transcript, audio_meta } = res.result || {}
            toast.success('笔记生成成功')
            updateTaskContent(task.id, {
              ...patch,
              markdown,
              transcript,
              audioMeta: audio_meta,
            })
            continue
          }

          updateTaskContent(task.id, patch)
        } catch (e) {
          console.error('❌ 任务轮询失败：', e)
          // toast.error(`生成失败 ${e.message || e}`)
          updateTaskContent(task.id, { status: 'FAILED' })
          // removeTask(task.id)
        }
      }
    }, interval)

    return () => clearInterval(timer)
  }, [interval])
}
