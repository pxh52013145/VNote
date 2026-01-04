import request from '@/utils/request'

export interface DifyConfigPayload {
  base_url: string
  dataset_id: string
  indexing_technique: string
  app_user: string
  timeout_seconds: number
  service_api_key_set: boolean
  app_api_key_set: boolean
  service_api_key_masked?: string
  app_api_key_masked?: string
  config_path?: string
}

export interface DifyConfigUpdateRequest {
  base_url?: string
  dataset_id?: string
  service_api_key?: string
  app_api_key?: string
  app_user?: string
  indexing_technique?: string
  timeout_seconds?: number
}

export const getDifyConfig = async () => {
  return (await request.get('/dify_config')) as DifyConfigPayload
}

export const updateDifyConfig = async (data: DifyConfigUpdateRequest) => {
  return (await request.post('/dify_config', data)) as DifyConfigPayload
}

