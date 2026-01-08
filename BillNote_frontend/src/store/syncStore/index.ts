import { create } from 'zustand'
import toast from 'react-hot-toast'

import {
  syncCopy,
  syncDeleteRemote,
  syncItemsCached,
  syncPull,
  syncPush,
  syncScan,
  SyncScanItem,
  SyncScanResponse,
} from '@/services/sync'

interface SyncStoreState {
  loading: boolean
  error: string | null
  profile: string | null
  minioBucket: string | null
  items: SyncScanItem[]
  lastScannedAt: string | null

  loadCached: (opts?: { silent?: boolean }) => Promise<void>
  scan: (opts?: { silent?: boolean }) => Promise<void>
  push: (item: SyncScanItem) => Promise<void>
  pull: (item: SyncScanItem, opts?: { overwrite?: boolean }) => Promise<string>
  deleteRemote: (item: SyncScanItem) => Promise<void>
  copyAsNew: (item: SyncScanItem, opts?: { fromSide?: 'local' | 'remote' }) => Promise<string>
  reset: () => void
}

export const useSyncStore = create<SyncStoreState>((set, get) => ({
  loading: false,
  error: null,
  profile: null,
  minioBucket: null,
  items: [],
  lastScannedAt: null,

  loadCached: async (opts?: { silent?: boolean }) => {
    set({ loading: true, error: null })
    try {
      const payload = (await syncItemsCached({ silent: opts?.silent ?? true })) as SyncScanResponse
      set({
        profile: payload?.profile || null,
        minioBucket: payload?.minio_bucket ?? null,
        items: Array.isArray(payload?.items) ? payload.items : [],
        lastScannedAt: payload?.last_scanned_at ? String(payload.last_scanned_at) : null,
      })
    } catch (e: any) {
      const msg = String(e?.msg || e?.message || 'åŒæ­¥çŠ¶æ€è¯»å–å¤±è´¥')
      set({ error: msg })
      if (!opts?.silent) toast.error(msg)
    } finally {
      set({ loading: false })
    }
  },

  scan: async (opts?: { silent?: boolean }) => {
    set({ loading: true, error: null })
    try {
      const payload = (await syncScan({ silent: opts?.silent ?? true })) as SyncScanResponse
      set({
        profile: payload?.profile || null,
        minioBucket: payload?.minio_bucket ?? null,
        items: Array.isArray(payload?.items) ? payload.items : [],
        lastScannedAt: payload?.last_scanned_at ? String(payload.last_scanned_at) : new Date().toISOString(),
      })
    } catch (e: any) {
      const msg = String(e?.msg || e?.message || '同步扫描失败')
      set({ error: msg })
      if (!opts?.silent) toast.error(msg)
    } finally {
      set({ loading: false })
    }
  },

  push: async (item: SyncScanItem) => {
    const id = String(item?.local_task_id || '').trim()
    if (!id) {
      toast.error('缺少本地任务 ID')
      return
    }

    const status = String(item?.status || '').toUpperCase()
    const includeNote = item?.local_has_note !== false
    const includeTranscript = item?.local_has_transcript !== false
    if (!includeNote && !includeTranscript) {
      toast.error('无可入库内容（本地缺少笔记/字幕）')
      return
    }

    const needBundle = item?.minio_bundle_exists === false
    const remoteHasAll =
      (includeNote ? Boolean(item?.remote_has_note) : true) && (includeTranscript ? Boolean(item?.remote_has_transcript) : true)
    const updateDify = !(needBundle && remoteHasAll)

    try {
      await syncPush(
        { item_id: id, include_note: includeNote, include_transcript: includeTranscript, update_dify: updateDify },
        { silent: true }
      )
      toast.success(status === 'SYNCED' ? '已重新入库' : '已同步至 Dify/MinIO')
      await get().scan({ silent: true })
    } catch (e: any) {
      const msg = String(e?.msg || e?.message || '入库失败')
      toast.error(msg)
    }
  },

  pull: async (item: SyncScanItem, opts?: { overwrite?: boolean }) => {
    const key = String(item?.source_key || '').trim()
    if (!key) {
      toast.error('缺少 source_key')
      return ''
    }
    try {
      const res = await syncPull({ source_key: key, overwrite: Boolean(opts?.overwrite) }, { silent: true })
      const taskId = String((res as any)?.task_id || (res as any)?.sync_id || '').trim()
      if (!taskId) {
        toast.error('同步获取成功，但未返回 task_id')
        return ''
      }
      toast.success('已获取到本地')
      await get().scan({ silent: true })
      return taskId
    } catch (e: any) {
      const msg = String(e?.msg || e?.message || '获取失败')
      toast.error(msg)
      return ''
    }
  },

  deleteRemote: async (item: SyncScanItem) => {
    const key = String(item?.source_key || '').trim()
    if (!key) {
      toast.error('缺少 source_key')
      return
    }

    try {
      await syncDeleteRemote(
        {
          source_key: key,
          delete_dify: true,
          dify_note_document_id: item?.dify_note_document_id ?? null,
          dify_transcript_document_id: item?.dify_transcript_document_id ?? null,
        },
        { silent: true }
      )
      toast.success('已标记远端删除（tombstone）')
      await get().scan({ silent: true })
    } catch (e: any) {
      const msg = String(e?.msg || e?.message || '删远端失败')
      toast.error(msg)
    }
  },

  copyAsNew: async (item: SyncScanItem, opts?: { fromSide?: 'local' | 'remote' }) => {
    const key = String(item?.source_key || '').trim()
    if (!key) {
      toast.error('缺少 source_key')
      return ''
    }
    const fromSide = opts?.fromSide || 'local'
    try {
      const res = await syncCopy(
        {
          source_key: key,
          from_side: fromSide,
          create_dify_docs: true,
          include_note: true,
          include_transcript: true,
        },
        { silent: true }
      )
      const taskId = String((res as any)?.task_id || (res as any)?.sync_id || '').trim()
      if (!taskId) {
        toast.error('副本创建成功，但未返回 task_id')
        return ''
      }
      toast.success('已另存为副本')
      await get().scan({ silent: true })
      return taskId
    } catch (e: any) {
      const msg = String(e?.msg || e?.message || '另存失败')
      toast.error(msg)
      return ''
    }
  },

  reset: () => {
    set({
      loading: false,
      error: null,
      profile: null,
      minioBucket: null,
      items: [],
      lastScannedAt: null,
    })
  },
}))
