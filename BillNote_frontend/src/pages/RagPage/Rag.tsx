import RagVideoPanel from '@/pages/RagPage/components/RagVideoPanel'
import RagChatPanel from '@/pages/RagPage/components/RagChatPanel'
import RagReferencesPanel from '@/pages/RagPage/components/RagReferencesPanel'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'

const RagPage = () => {
  return (
    <ResizablePanelGroup direction="horizontal" autoSaveId="rag-layout" className="h-full w-full">
      <ResizablePanel defaultSize={24} minSize={16} maxSize={40}>
        <div className="flex h-full flex-col border-r border-slate-200 bg-white shadow-sm">
          <RagVideoPanel />
        </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      <ResizablePanel defaultSize={56} minSize={30}>
        <div className="relative flex h-full flex-col bg-slate-50/50">
          <RagChatPanel />
        </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      <ResizablePanel defaultSize={20} minSize={16} collapsible collapsedSize={0}>
        <div className="flex h-full flex-col border-l border-slate-200 bg-white">
          <RagReferencesPanel />
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

export default RagPage
