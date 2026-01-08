import request, { RequestConfig } from '@/utils/request'

export type SyncStatus =
  | 'LOCAL_ONLY'
  | 'DIFY_ONLY'
  | 'DIFY_ONLY_NO_BUNDLE'
  | 'SYNCED'
  | 'PARTIAL'
  | 'CONFLICT'
  | 'DELETED'
  | 'DIFY_ONLY_LEGACY'

export interface SyncScanItem {
  status: SyncStatus | string
  title: string
  platform: string
  video_id: string
  created_at_ms?: number | null
  source_key?: string | null
  sync_id?: string | null
  local_task_id?: string | null
  local_has_note?: boolean | null
  local_has_transcript?: boolean | null
  dify_note_document_id?: string | null
  dify_note_name?: string | null
  dify_transcript_document_id?: string | null
  dify_transcript_name?: string | null
  remote_has_note?: boolean | null
  remote_has_transcript?: boolean | null
  minio_bundle_exists?: boolean | null
  minio_tombstone_exists?: boolean | null
}

export interface SyncScanResponse {
  profile: string
  dify_base_url: string
  note_dataset_id: string
  transcript_dataset_id: string
  minio_bucket?: string | null
  last_scanned_at?: string | null
  items: SyncScanItem[]
}

export const syncScan = async (opts?: { silent?: boolean }) => {
  const config: RequestConfig | undefined = opts?.silent ? { silent: true } : undefined
  return await request.post('/sync/scan', {}, config)
}

export const syncItemsCached = async (opts?: { silent?: boolean }) => {
  const config: RequestConfig | undefined = opts?.silent ? { silent: true } : undefined
  return await request.get('/sync/items', config)
}

export const syncPush = async (
  data: { item_id: string; include_transcript?: boolean; include_note?: boolean; update_dify?: boolean },
  opts?: { silent?: boolean }
) => {
  const config: RequestConfig | undefined = opts?.silent ? { silent: true } : undefined
  return await request.post('/sync/push', data, config)
}

export const syncPull = async (
  data: { source_key: string; overwrite?: boolean },
  opts?: { silent?: boolean }
) => {
  const config: RequestConfig | undefined = opts?.silent ? { silent: true } : undefined
  return await request.post('/sync/pull', data, config)
}

export const syncDeleteRemote = async (
  data: {
    source_key: string
    delete_dify?: boolean
    dify_note_document_id?: string | null
    dify_transcript_document_id?: string | null
  },
  opts?: { silent?: boolean }
) => {
  const config: RequestConfig | undefined = opts?.silent ? { silent: true } : undefined
  return await request.post('/sync/delete_remote', data, config)
}

export const syncCopy = async (
  data: {
    source_key: string
    from_side?: 'local' | 'remote'
    create_dify_docs?: boolean
    include_transcript?: boolean
    include_note?: boolean
    new_created_at_ms?: number
  },
  opts?: { silent?: boolean }
) => {
  const config: RequestConfig | undefined = opts?.silent ? { silent: true } : undefined
  return await request.post('/sync/copy', data, config)
}
