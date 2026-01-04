import { create } from 'zustand'
import { v4 as uuidv4 } from 'uuid'

import type { RagRetrieverResource } from '@/services/rag'
import {
  appendRagMessage,
  clearRagConversations,
  deleteRagConversation,
  getRagState,
  setRagCurrentConversation,
  setRagState,
  upsertRagConversation,
} from '@/services/ragHistory'

export type RagChatRole = 'user' | 'assistant'

export interface RagChatMessage {
  id: string
  role: RagChatRole
  content: string
  createdAt: string
  resources?: RagRetrieverResource[]
}

export interface RagConversation {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  difyConversationId?: string
  messages: RagChatMessage[]
}

interface RagStore {
  userId: string
  conversations: RagConversation[]
  currentConversationId: string | null
  initialized: boolean
  initializing: boolean

  bootstrap: () => Promise<void>

  createConversation: (title?: string) => string
  updateConversation: (id: string, patch: Partial<Omit<RagConversation, 'id' | 'createdAt' | 'messages'>>) => void
  removeConversation: (id: string) => void
  clearConversations: () => void

  setCurrentConversation: (id: string | null) => void
  getCurrentConversation: () => RagConversation | null

  appendMessage: (conversationId: string, msg: RagChatMessage) => void
}

const DEFAULT_CONVERSATION_TITLE = '新对话'
const MAX_CONVERSATIONS = 30
const MAX_MESSAGES_PER_CONVERSATION = 60

const normalizeTitle = (text: string) => {
  const t = (text || '').replace(/\s+/g, ' ').trim()
  if (!t) return DEFAULT_CONVERSATION_TITLE
  return t.length > 32 ? t.slice(0, 32) + '…' : t
}

const pickConversation = (conversations: any[]): RagConversation[] => {
  if (!Array.isArray(conversations)) return []
  return conversations
    .filter(c => c && typeof c === 'object')
    .map((c: any) => ({
      id: String(c.id || ''),
      title: String(c.title || DEFAULT_CONVERSATION_TITLE),
      createdAt: String(c.createdAt || c.created_at || new Date().toISOString()),
      updatedAt: String(c.updatedAt || c.updated_at || c.createdAt || new Date().toISOString()),
      difyConversationId: c.difyConversationId || c.dify_conversation_id,
      messages: Array.isArray(c.messages) ? c.messages : [],
    }))
    .filter(c => c.id)
}

const tryReadLegacyLocalStorage = () => {
  try {
    const raw = window.localStorage.getItem('rag-storage')
    if (!raw) return null
    const parsed = JSON.parse(raw)
    const state = parsed?.state || parsed
    return {
      user_id: state?.userId,
      current_conversation_id: state?.currentConversationId,
      conversations: state?.conversations,
    }
  } catch {
    return null
  }
}

export const useRagStore = create<RagStore>()((set, get) => ({
  userId: '',
  conversations: [],
  currentConversationId: null,
  initialized: false,
  initializing: false,

  bootstrap: async () => {
    if (get().initialized || get().initializing) return
    set({ initializing: true })
    try {
      let state = await getRagState()

      const hasServerHistory = Array.isArray(state?.conversations) && state.conversations.length > 0
      if (!hasServerHistory && typeof window !== 'undefined') {
        const legacy = tryReadLegacyLocalStorage()
        const legacyConversations = pickConversation(legacy?.conversations)
        if (legacyConversations.length > 0) {
          await setRagState({
            user_id: legacy?.user_id,
            current_conversation_id: legacy?.current_conversation_id,
            conversations: legacyConversations,
          })
          window.localStorage.removeItem('rag-storage')
          state = await getRagState()
        }
      }

      const conversations = pickConversation(state?.conversations)
      const currentConversationId =
        state?.current_conversation_id || (conversations.length > 0 ? conversations[0].id : null)
      const currentConversation = currentConversationId
        ? conversations.find(c => c.id === currentConversationId) || null
        : null
      const shouldStartNewConversationOnBoot = !!(
        currentConversation &&
        Array.isArray(currentConversation.messages) &&
        currentConversation.messages.length > 0
      )
      set({
        userId: String(state?.user_id || ''),
        conversations,
        currentConversationId,
        initialized: true,
        initializing: false,
      })

      // If the last opened conversation already has messages, start with a fresh one on app restart.
      // This keeps history, but avoids showing "last session state" as the active chat.
      if (shouldStartNewConversationOnBoot) {
        get().createConversation()
      }
    } catch (e) {
      console.error('Failed to bootstrap RAG history:', e)
      set({ initialized: true, initializing: false })
    }
  },

  createConversation: (title?: string) => {
    const id = uuidv4()
    const now = new Date().toISOString()
    const conv: RagConversation = {
      id,
      title: normalizeTitle(title || DEFAULT_CONVERSATION_TITLE),
      createdAt: now,
      updatedAt: now,
      messages: [],
    }

    set(state => ({
      conversations: [conv, ...state.conversations].slice(0, MAX_CONVERSATIONS),
      currentConversationId: id,
    }))

    void upsertRagConversation(id, { title: conv.title }).catch(e => console.error(e))
    void setRagCurrentConversation(id).catch(e => console.error(e))

    return id
  },

  updateConversation: (id, patch) => {
    set(state => ({
      conversations: state.conversations.map(c =>
        c.id === id ? { ...c, ...patch, updatedAt: new Date().toISOString() } : c
      ),
    }))

    void upsertRagConversation(id, patch as any)
      .then((conv: any) => {
        if (!conv || typeof conv !== 'object') return
        set(state => ({
          conversations: state.conversations.map(c =>
            c.id === id
              ? {
                  ...c,
                  title: String(conv.title || c.title),
                  updatedAt: String(conv.updatedAt || c.updatedAt),
                  difyConversationId: conv.difyConversationId || c.difyConversationId,
                }
              : c
          ),
        }))
      })
      .catch(e => console.error(e))
  },

  removeConversation: id => {
    const prev = get().conversations
    set(state => {
      const conversations = state.conversations.filter(c => c.id !== id)
      const currentConversationId =
        state.currentConversationId === id ? (conversations[0]?.id ?? null) : state.currentConversationId
      return { conversations, currentConversationId }
    })

    void deleteRagConversation(id)
      .then(state => {
        const conversations = pickConversation(state?.conversations)
        set({
          userId: String(state?.user_id || get().userId || ''),
          conversations,
          currentConversationId:
            state?.current_conversation_id || (conversations.length > 0 ? conversations[0].id : null),
        })
      })
      .catch(e => {
        console.error(e)
        set({ conversations: prev })
      })
  },

  clearConversations: () => {
    const prev = get().conversations
    set({ conversations: [], currentConversationId: null })
    void clearRagConversations()
      .then(state => {
        const conversations = pickConversation(state?.conversations)
        set({
          userId: String(state?.user_id || get().userId || ''),
          conversations,
          currentConversationId:
            state?.current_conversation_id || (conversations.length > 0 ? conversations[0].id : null),
        })
      })
      .catch(e => {
        console.error(e)
        set({ conversations: prev })
      })
  },

  setCurrentConversation: id => {
    set({ currentConversationId: id })
    if (id) {
      void setRagCurrentConversation(id).catch(e => console.error(e))
    }
  },

  getCurrentConversation: () => {
    const currentConversationId = get().currentConversationId
    return get().conversations.find(c => c.id === currentConversationId) || null
  },

  appendMessage: (conversationId, msg) => {
    set(state => ({
      conversations: state.conversations.map(c => {
        if (c.id !== conversationId) return c

        const nextMessages = [...(c.messages || []), msg].slice(-MAX_MESSAGES_PER_CONVERSATION)
        const nextTitle =
          c.title === DEFAULT_CONVERSATION_TITLE && msg.role === 'user' ? normalizeTitle(msg.content) : c.title

        return { ...c, title: nextTitle, messages: nextMessages, updatedAt: new Date().toISOString() }
      }),
    }))

    void appendRagMessage(conversationId, msg)
      .then((conv: any) => {
        if (!conv || typeof conv !== 'object') return
        set(state => ({
          conversations: state.conversations.map(c =>
            c.id === conversationId
              ? {
                  ...c,
                  title: String(conv.title || c.title),
                  updatedAt: String(conv.updatedAt || c.updatedAt),
                  difyConversationId: conv.difyConversationId || c.difyConversationId,
                  messages: Array.isArray(conv.messages) ? conv.messages : c.messages,
                }
              : c
          ),
        }))
      })
      .catch(e => console.error(e))
  },
}))
