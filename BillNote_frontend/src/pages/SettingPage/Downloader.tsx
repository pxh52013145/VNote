import Provider from '@/components/Form/modelForm/Provider.tsx'
import { Outlet } from 'react-router-dom'
import Options from '@/components/Form/DownloaderForm/Options.tsx'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
const Downloader = () => {
  return (
    <ResizablePanelGroup direction="horizontal" autoSaveId="settings-downloader-layout" className="h-full bg-white">
      <ResizablePanel defaultSize={25} minSize={16} maxSize={40}>
        <div className="h-full border-r border-neutral-200 p-2">
          <Options />
        </div>
      </ResizablePanel>

      <ResizableHandle withHandle />

      <ResizablePanel defaultSize={75} minSize={30}>
        <div className="h-full">
          <Outlet />
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
export default Downloader
