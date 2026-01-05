import { useEffect, useMemo, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Menu } from 'lucide-react'
import type { ImperativePanelHandle } from 'react-resizable-panels'

import AppSidebar from '@/components/AppSidebar'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'

const getTitle = (pathname: string) => {
  if (pathname.startsWith('/note')) return '知识库'
  if (pathname.startsWith('/rag')) return 'RAG 视频问答'
  if (pathname.startsWith('/settings')) return '设置'
  return 'RAGVideo'
}

const AppShellLayout = () => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true)
  const sidebarPanelRef = useRef<ImperativePanelHandle>(null)
  const location = useLocation()
  const title = useMemo(() => getTitle(location.pathname), [location.pathname])

  useEffect(() => {
    const panel = sidebarPanelRef.current
    if (!panel) return
    setIsSidebarOpen(!panel.isCollapsed())
  }, [])

  const toggleSidebar = () => {
    const panel = sidebarPanelRef.current
    if (!panel) return
    if (panel.isCollapsed()) panel.expand()
    else panel.collapse()
  }

  return (
    <ResizablePanelGroup
      direction="horizontal"
      autoSaveId="app-shell-layout"
      className="h-screen w-full overflow-hidden bg-slate-50 text-slate-900 font-sans"
    >
      <ResizablePanel
        ref={sidebarPanelRef}
        defaultSize={18}
        minSize={10}
        maxSize={30}
        collapsible
        collapsedSize={4}
        onCollapse={() => setIsSidebarOpen(false)}
        onExpand={() => setIsSidebarOpen(true)}
      >
        <AppSidebar isOpen={isSidebarOpen} toggleOpen={toggleSidebar} />
      </ResizablePanel>

      <ResizableHandle withHandle className="z-30" />

      <ResizablePanel defaultSize={82} minSize={40}>
        <main className="flex h-full w-full flex-col overflow-hidden transition-all duration-300">
        <header className="h-14 bg-white border-b border-slate-200 flex items-center px-4 justify-between lg:hidden">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={toggleSidebar}
              className="p-2 hover:bg-slate-100 rounded-md"
            >
              <Menu className="w-5 h-5 text-slate-600" />
            </button>
            <span className="font-semibold text-slate-800">{title}</span>
          </div>
        </header>

        <div className="flex-1 overflow-hidden relative">
          <Outlet />
        </div>
      </main>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

export default AppShellLayout
