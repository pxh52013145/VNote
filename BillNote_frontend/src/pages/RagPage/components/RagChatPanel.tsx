import { useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { v4 as uuidv4 } from 'uuid'
import { ChevronDown, Clock, Loader2, MessageSquare, Plus, Send, X } from 'lucide-react'

import { ragChat } from '@/services/rag'
import { useRagStore, type RagChatMessage } from '@/store/ragStore'
import { useTaskStore } from '@/store/taskStore'
import RagConversationList from '@/pages/RagPage/components/RagConversationList'
import { extractQueryKeywords } from '@/utils/ragKeywords'

const isDifyIndexingCompleted = (payload: any) => {
  const docs = payload?.data
  if (!Array.isArray(docs) || docs.length === 0) return false
  return docs.every(d => typeof d === 'object' && d && d.indexing_status === 'completed')
}

const RagChatPanel = () => {
  const userId = useRagStore(state => state.userId)
  const conversations = useRagStore(state => state.conversations)
  const currentConversationId = useRagStore(state => state.currentConversationId)
  const initialized = useRagStore(state => state.initialized)
  const bootstrap = useRagStore(state => state.bootstrap)
  const createConversation = useRagStore(state => state.createConversation)
  const appendMessage = useRagStore(state => state.appendMessage)
  const updateConversation = useRagStore(state => state.updateConversation)
  const setSelectedReferenceMessage = useRagStore(state => state.setSelectedReferenceMessage)
  const selectedReferenceMessageId = useRagStore(state =>
    state.currentConversationId ? state.selectedReferenceByConversation[state.currentConversationId] || null : null
  )

  const tasks = useTaskStore(state => state.tasks)

  const indexedCount = useMemo(() => {
    return tasks.filter(t => t.dify?.batch && isDifyIndexingCompleted(t.dify_indexing)).length
  }, [tasks])

  const currentConversation = useMemo(() => {
    return conversations.find(c => c.id === currentConversationId) || null
  }, [conversations, currentConversationId])

  const messages = currentConversation?.messages || []
  const effectiveSelectedReferenceMessageId = useMemo(() => {
    if (!currentConversationId) return null
    if (selectedReferenceMessageId) return selectedReferenceMessageId
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === 'assistant') return messages[i]?.id || null
    }
    return null
  }, [currentConversationId, messages, selectedReferenceMessageId])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  useEffect(() => {
    if (!initialized) return
    if (!currentConversationId && conversations.length === 0) {
      createConversation()
    }
  }, [initialized, currentConversationId, conversations.length, createConversation])

  const bottomRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length])

  const canSend = useMemo(() => input.trim().length > 0 && !sending && !!userId, [input, sending, userId])

  const resetConversation = () => {
    createConversation()
    toast.success('已开启新对话')
  }

  const send = async () => {
    const query = input.trim()
    if (!query) return

    setInput('')
    setSending(true)

    const convId = currentConversationId || createConversation()
    const difyConversationId = currentConversation?.difyConversationId

    const userMsg: RagChatMessage = {
      id: uuidv4(),
      role: 'user',
      content: query,
      createdAt: new Date().toISOString(),
    }
    appendMessage(convId, userMsg)

    try {
      const resp = await ragChat({
        query,
        conversation_id: difyConversationId,
        user: userId,
      })

      if (resp.conversation_id) {
        updateConversation(convId, { difyConversationId: resp.conversation_id })
      }

      const keywords = extractQueryKeywords(query)
      const assistantMsg: RagChatMessage = {
        id: resp.message_id || uuidv4(),
        role: 'assistant',
        content: resp.answer || '',
        createdAt: new Date().toISOString(),
        query,
        keywords,
        replyTo: userMsg.id,
        resources: resp.retriever_resources || [],
      }
      appendMessage(convId, assistantMsg)
      setSelectedReferenceMessage(convId, assistantMsg.id)
    } catch (e) {
      console.error(e)
      // `request` already shows an error toast (network/backend). Avoid double-toasting here.
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col h-full relative">
      <div className="h-16 border-b border-slate-200 bg-white/80 backdrop-blur-sm flex items-center px-6 justify-between sticky top-0 z-10">
        <button
          type="button"
          onClick={() => setIsHistoryOpen(true)}
          className="flex items-center gap-3 group hover:bg-slate-50 p-2 -ml-2 rounded-xl transition-all"
        >
          <div className="w-8 h-8 rounded-full bg-primary-light flex items-center justify-center text-primary group-hover:bg-primary group-hover:text-primary-foreground transition-colors">
            <MessageSquare className="w-4 h-4" />
          </div>
          <div className="text-left">
            <h2 className="font-semibold text-slate-800 flex items-center gap-1.5 group-hover:text-primary transition-colors">
              RAG 对话
              <ChevronDown className="w-3.5 h-3.5 text-slate-400 group-hover:text-primary mt-0.5" />
            </h2>
            <p className="text-xs text-slate-500">
              {indexedCount > 0 ? `上下文：已入库 ${indexedCount} 个视频` : '上下文：请先生成笔记并入库'}
            </p>
          </div>
        </button>

        <button
          type="button"
          onClick={resetConversation}
          className="text-sm text-slate-500 hover:text-primary flex items-center gap-1 bg-white border border-slate-200 px-3 py-1.5 rounded-full hover:shadow-sm transition-all"
          disabled={sending}
        >
          <Plus className="w-4 h-4" />
          新对话
        </button>
      </div>

      {isHistoryOpen && (
        <div
          className="absolute inset-0 z-50 bg-slate-900/10 backdrop-blur-[2px] flex items-start justify-center pt-20"
          onClick={() => setIsHistoryOpen(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-[420px] max-h-[620px] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200"
            onClick={e => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50">
              <h3 className="font-semibold text-slate-800 flex items-center gap-2">
                <Clock className="w-4 h-4 text-slate-500" />
                对话历史
              </h3>
              <button
                type="button"
                onClick={() => setIsHistoryOpen(false)}
                className="p-1 hover:bg-slate-200 rounded-full transition-colors"
              >
                <X className="w-4 h-4 text-slate-500" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              <RagConversationList onPicked={() => setIsHistoryOpen(false)} />
            </div>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6 space-y-8">
        {messages.length === 0 ? (
          <div className="flex gap-4 max-w-3xl mx-auto">
            <div className="w-8 h-8 rounded-full bg-primary flex-shrink-0 flex items-center justify-center text-primary-foreground text-xs font-bold">
              AI
            </div>
            <div className="space-y-2">
              <div className="bg-white p-4 rounded-2xl rounded-tl-none border border-slate-200 shadow-sm text-slate-700 text-sm leading-relaxed">
                {indexedCount > 0 ? (
                  <>
                    <p>你好！我可以基于你的“视频笔记 + 转写原文”做检索问答。</p>
                    <ul className="list-disc list-inside mt-2 space-y-1 text-slate-600">
                      <li>“帮我定位：哪里讲到了「某个关键词」？给出时间戳。”</li>
                      <li>“总结这段视频的关键知识点，并引用原文片段。”</li>
                      <li>“「某个概念」和哪些片段相关？给出关联处（带时间戳）。”</li>
                    </ul>
                  </>
                ) : (
                  <>
                    <p>当前还没有可检索的入库内容。</p>
                    <p className="mt-2 text-slate-600">
                      先在左侧添加视频并点击“生成笔记”（会自动写入 Dify 知识库），等待状态显示“已入库”后再来提问。
                    </p>
                    <p className="mt-3 text-slate-600">入库后你可以问：</p>
                    <ul className="list-disc list-inside mt-2 space-y-1 text-slate-600">
                      <li>“帮我定位：哪里讲到了「某个关键词」？给出时间戳。”</li>
                      <li>“把这段视频的知识点按层次列出来，并引用原文。”</li>
                      <li>“这段内容和哪些片段相关？给出关联处（带时间戳）。”</li>
                    </ul>
                  </>
                )}
              </div>
            </div>
          </div>
        ) : (
          messages.map(m => {
            if (m.role === 'user') {
              return (
                <div key={m.id} className="flex gap-4 max-w-3xl mx-auto flex-row-reverse">
                  <div className="w-8 h-8 rounded-full bg-slate-200 flex-shrink-0 flex items-center justify-center text-slate-600 text-xs font-bold">
                    我
                  </div>
                  <div className="space-y-2">
                    <div className="bg-primary p-4 rounded-2xl rounded-tr-none shadow-md text-primary-foreground text-sm leading-relaxed whitespace-pre-wrap">
                      {m.content}
                    </div>
                  </div>
                </div>
              )
            }

            const isSelected = !!(currentConversationId && effectiveSelectedReferenceMessageId === m.id)

            return (
              <div key={m.id} className="flex gap-4 max-w-3xl mx-auto">
                <div className="w-8 h-8 rounded-full bg-primary flex-shrink-0 flex items-center justify-center text-primary-foreground text-xs font-bold">
                  AI
                </div>
                <div className="space-y-2 w-full">
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() =>
                      currentConversationId ? setSelectedReferenceMessage(currentConversationId, m.id) : undefined
                    }
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        if (!currentConversationId) return
                        setSelectedReferenceMessage(currentConversationId, m.id)
                      }
                    }}
                    className={[
                      'bg-white p-4 rounded-2xl rounded-tl-none border shadow-sm text-slate-700 text-sm leading-relaxed whitespace-pre-wrap cursor-pointer',
                      isSelected ? 'border-primary/40 ring-2 ring-primary/10' : 'border-slate-200',
                    ].join(' ')}
                    title="点击查看该条回复的引用"
                  >
                    {m.content}
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => (currentConversationId ? setSelectedReferenceMessage(currentConversationId, m.id) : undefined)}
                      className={[
                        'text-xs px-2 py-1 rounded border transition-colors',
                        isSelected
                          ? 'bg-primary-light text-primary border-primary/30'
                          : 'bg-slate-100 text-slate-500 border-slate-200 hover:bg-slate-200',
                      ].join(' ')}
                      title="点击查看该条回复的引用"
                    >
                      {m.resources && m.resources.length > 0 ? `引用：${m.resources.length} 条` : '无引用'}
                    </button>
                  </div>
                </div>
              </div>
            )
          })
        )}
        {sending && (
          <div className="flex gap-4 max-w-3xl mx-auto">
            <div className="w-8 h-8 rounded-full bg-primary flex-shrink-0 flex items-center justify-center text-primary-foreground text-xs font-bold">
              AI
            </div>
            <div className="space-y-2 w-full">
              <div className="bg-white p-4 rounded-2xl rounded-tl-none border border-slate-200 shadow-sm text-slate-700 text-sm leading-relaxed">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                  <span className="text-slate-500">正在检索知识库并生成回答…</span>
                </div>
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="p-6 bg-white border-t border-slate-200">
        <div className="max-w-3xl mx-auto relative">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                if (canSend) void send()
              }
            }}
            className="w-full pl-4 pr-12 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all text-sm resize-none shadow-inner text-slate-900 placeholder:text-slate-400"
            placeholder="关于视频提问（Shift+Enter 换行）..."
            rows={1}
            style={{ minHeight: '50px' }}
            disabled={sending}
          />
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            className="absolute right-2 bottom-2 p-2 bg-primary hover:opacity-95 disabled:opacity-50 disabled:hover:opacity-50 text-primary-foreground rounded-lg transition-colors shadow-sm"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
        <p className="text-center text-xs text-slate-400 mt-2">AI 可能会犯错，请核对重要信息。</p>
      </div>
    </div>
  )
}

export default RagChatPanel
