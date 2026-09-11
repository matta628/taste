import { AnalyticsChat } from './AnalyticsChat'
import { useUIStore } from '../../store/uiStore'

// What the analytics pages add on top of the app shell: the Ask-Claude panel
// that slides in from the right. The nav and banners live in AppShell now, so
// analytics is a section of the app rather than a separate one.
export function AnalyticsShell({ children }) {
  const { chatPanelOpen } = useUIStore()

  return (
    <div className="h-full flex flex-col">
      <div className={`flex-1 overflow-hidden transition-all duration-200 ${chatPanelOpen ? 'mr-[360px]' : ''}`}>
        {children}
      </div>
      <AnalyticsChat />
    </div>
  )
}
