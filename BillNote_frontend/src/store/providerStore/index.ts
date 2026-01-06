import { create } from 'zustand'
import { IProvider } from '@/types'
import {
  addProvider,
  getProviderById as fetchProviderById,
  getProviderList,
  deleteProviderById,
  updateProviderById,
} from '@/services/model.ts'

type ProviderUpdate = { id: string } & Partial<Omit<IProvider, 'id'>>
type ProviderCreate = Pick<IProvider, 'name' | 'baseUrl' | 'type'> &
  Partial<Pick<IProvider, 'apiKey' | 'logo'>>

interface ProviderStore {
  provider: IProvider[]
  setProvider: (provider: IProvider) => void
  setAllProviders: (providers: IProvider[]) => void
  getProviderById: (id: string) => IProvider | undefined
  getProviderList: () => IProvider[]
  fetchProviderList: () => Promise<void>
  loadProviderById: (id: string) => Promise<IProvider | undefined>
  addNewProvider: (provider: ProviderCreate) => Promise<string | undefined>
  updateProvider: (provider: ProviderUpdate) => Promise<void>
  deleteProvider: (id: string) => Promise<void>
}

export const useProviderStore = create<ProviderStore>((set, get) => ({
  provider: [],

  setProvider: newProvider =>
    set(state => {
      const exists = state.provider.find(p => p.id === newProvider.id)
      if (exists) {
        return {
          provider: state.provider.map(p => (p.id === newProvider.id ? newProvider : p)),
        }
      }
      return { provider: [...state.provider, newProvider] }
    }),

  setAllProviders: providers => set({ provider: providers }),

  loadProviderById: async (id: string) => {
    const cached = get().provider.find(p => p.id === id)
    if (cached) {
      return cached
    }

    try {
      const item = await fetchProviderById(id)
      if (!item) return undefined

      return {
        id: item.id,
        name: item.name,
        logo: item.logo,
        apiKey: '',
        baseUrl: item.base_url,
        type: item.type,
        enabled: item.enabled,
      }
    } catch (error) {
      console.error('Error fetching provider:', error)
      return undefined
    }
  },

  addNewProvider: async (provider: ProviderCreate) => {
    const payload = {
      name: provider.name,
      api_key: provider.apiKey ?? '',
      base_url: provider.baseUrl,
      logo: provider.logo,
      type: provider.type,
    }

    try {
      const id = await addProvider(payload)
      await get().fetchProviderList()
      return id
    } catch (error) {
      console.error('Error fetching provider:', error)
      return undefined
    }
  },

  getProviderById: id => get().provider.find(p => p.id === id),

  updateProvider: async (provider: ProviderUpdate) => {
    try {
      const data: Record<string, unknown> = { id: provider.id }
      if (provider.name !== undefined) data.name = provider.name
      if (provider.logo !== undefined) data.logo = provider.logo
      if (provider.type !== undefined) data.type = provider.type
      if (provider.enabled !== undefined) data.enabled = provider.enabled
      if (provider.baseUrl !== undefined) data.base_url = provider.baseUrl
      if (provider.apiKey !== undefined && provider.apiKey !== '') data.api_key = provider.apiKey

      await updateProviderById(data)
      await get().fetchProviderList()
    } catch (error) {
      console.error('Error fetching provider:', error)
    }
  },

  deleteProvider: async (id: string) => {
    await deleteProviderById(id)
    set(state => ({ provider: state.provider.filter(p => p.id !== id) }))
    await get().fetchProviderList()
  },

  getProviderList: () => get().provider,

  fetchProviderList: async () => {
    try {
      const res = await getProviderList()
      set({
        provider: res.map(
          (item: {
            id: string
            name: string
            logo: string
            api_key: string
            base_url: string
            type: string
            enabled: number
          }) => ({
            id: item.id,
            name: item.name,
            logo: item.logo,
            apiKey: item.api_key,
            baseUrl: item.base_url,
            type: item.type,
            enabled: item.enabled,
          })
        ),
      })
    } catch (error) {
      console.error('Error fetching provider list:', error)
    }
  },
}))
