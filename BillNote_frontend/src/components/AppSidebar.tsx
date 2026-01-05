import { Link, NavLink } from 'react-router-dom'
import { ChevronLeft, ChevronRight, FileText, MessageSquareText, Play, Settings } from 'lucide-react'

interface AppSidebarProps {
  isOpen: boolean
  toggleOpen: () => void
}

const navItemBase =
  'w-full flex items-center p-3 rounded-lg transition-all duration-200 group'

const AppSidebar = ({ isOpen, toggleOpen }: AppSidebarProps) => {
  return (
    <aside
      className={[
        'bg-slate-900 text-white flex flex-col border-r border-slate-800 transition-all duration-300 ease-in-out relative',
        'w-full min-w-0',
      ].join(' ')}
    >
      <div className="h-16 flex items-center px-4 border-b border-slate-800/50">
        <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center shrink-0">
          <Play className="w-4 h-4 fill-white text-white" />
        </div>
        <span
          className={[
            'ml-3 font-bold text-lg tracking-tight whitespace-nowrap overflow-hidden transition-opacity duration-200',
            isOpen ? 'opacity-100' : 'opacity-0 w-0',
          ].join(' ')}
        >
          RAG<span className="text-brand-400">Video</span>
        </span>
      </div>

      <nav className="flex-1 py-6 px-2 space-y-2">
        <NavLink
          to="/rag"
          className={({ isActive }) =>
            [
              navItemBase,
              isActive
                ? 'bg-brand-600 text-white shadow-lg shadow-brand-900/20'
                : 'text-slate-400 hover:bg-slate-800 hover:text-white',
              isOpen ? 'justify-start' : 'justify-center',
            ].join(' ')
          }
          title={!isOpen ? 'RAG 问答' : undefined}
        >
          <MessageSquareText className="w-5 h-5" />
          <span
            className={[
              'ml-3 font-medium whitespace-nowrap overflow-hidden transition-all duration-200',
              isOpen ? 'w-auto opacity-100' : 'w-0 opacity-0',
            ].join(' ')}
          >
            RAG 问答
          </span>
        </NavLink>

        <NavLink
          to="/note"
          className={({ isActive }) =>
            [
              navItemBase,
              isActive
                ? 'bg-brand-600 text-white shadow-lg shadow-brand-900/20'
                : 'text-slate-400 hover:bg-slate-800 hover:text-white',
              isOpen ? 'justify-start' : 'justify-center',
            ].join(' ')
          }
          title={!isOpen ? '知识库' : undefined}
        >
          <FileText className="w-5 h-5" />
          <span
            className={[
              'ml-3 font-medium whitespace-nowrap overflow-hidden transition-all duration-200',
              isOpen ? 'w-auto opacity-100' : 'w-0 opacity-0',
            ].join(' ')}
          >
            知识库
          </span>
        </NavLink>
      </nav>

      <div className="p-2 border-t border-slate-800/50">
        <Link
          to="/settings"
          className={[
            navItemBase,
            'text-slate-400 hover:bg-slate-800 hover:text-white',
            isOpen ? 'justify-start' : 'justify-center',
          ].join(' ')}
          title={!isOpen ? '设置' : undefined}
        >
          <Settings className="w-5 h-5" />
          <span
            className={[
              'ml-3 font-medium whitespace-nowrap overflow-hidden transition-all duration-200',
              isOpen ? 'w-auto opacity-100' : 'w-0 opacity-0',
            ].join(' ')}
          >
            设置
          </span>
        </Link>

        <button
          type="button"
          onClick={toggleOpen}
          className="w-full mt-2 flex items-center justify-center p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-md transition-colors"
        >
          {isOpen ? <ChevronLeft className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
        </button>
      </div>
    </aside>
  )
}

export default AppSidebar
