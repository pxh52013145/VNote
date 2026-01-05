import { Outlet } from 'react-router-dom'
import React from 'react'
import logo from '@/assets/icon.svg'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'

interface ISettingLayoutProps {
  Menu: React.ReactNode
}
const SettingLayout = ({ Menu }: ISettingLayoutProps) => {
  return (
    <ResizablePanelGroup direction="horizontal" autoSaveId="settings-layout" className="h-full w-full bg-slate-50 text-slate-900">
      <ResizablePanel defaultSize={25} minSize={16} maxSize={40}>
        <aside className="flex h-full flex-col border-r border-slate-200 bg-white shadow-sm">
        <header className="h-16 px-6 border-b border-slate-100 flex items-center gap-3">
          <div className="h-9 w-9 rounded-xl overflow-hidden bg-slate-100 flex items-center justify-center">
            <img src={logo} alt="logo" className="h-full w-full object-contain" />
          </div>
          <div className="font-semibold text-slate-800">设置</div>
        </header>

        <div className="flex-1 overflow-auto p-4">{Menu}</div>
      </aside>

      </ResizablePanel>

      <ResizableHandle withHandle />

      <ResizablePanel defaultSize={75} minSize={30}>
        <main className="h-full overflow-hidden bg-white">
          <Outlet />
        </main>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
export default SettingLayout
