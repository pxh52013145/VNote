'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { cn } from '@/lib/utils'
import { useTaskStore } from '@/store/taskStore'

interface Segment {
  start: number
  end: number
  text: string
  speaker?: string
}

interface Task {
  transcript?: {
    segments?: Segment[]
  }
}

export interface TranscriptViewerProps {
  seekSeconds?: number
  seekNonce?: number
}

const findSegmentIndexByTime = (segments: Segment[], targetSeconds: number): number => {
  if (segments.length === 0) return 0

  const target = Math.max(0, targetSeconds)
  let index = 0

  for (let i = 0; i < segments.length; i += 1) {
    if (segments[i].start <= target) index = i
    else break
  }

  return index
}

const formatTime = (totalSeconds: number): string => {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

const TranscriptViewer = ({ seekSeconds = 0, seekNonce = 0 }: TranscriptViewerProps) => {
  const getCurrentTask = useTaskStore(state => state.getCurrentTask)
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const [task, setTask] = useState<Task | null>(null)
  const [activeSegment, setActiveSegment] = useState<number | null>(null)
  const segmentRefs = useRef<(HTMLDivElement | null)[]>([])

  useEffect(() => {
    setTask(getCurrentTask())
  }, [currentTaskId, getCurrentTask])

  const segments = useMemo(() => task?.transcript?.segments ?? [], [task])
  const durationSeconds = segments.length > 0 ? segments[segments.length - 1]?.end || 0 : 0

  const scrollToSegment = useCallback((index: number, behavior: ScrollBehavior) => {
    segmentRefs.current[index]?.scrollIntoView({ behavior, block: 'center' })
  }, [])

  useEffect(() => {
    if (segments.length === 0) return

    const index = findSegmentIndexByTime(segments, seekSeconds)
    setActiveSegment(index)
    requestAnimationFrame(() => scrollToSegment(index, 'auto'))
  }, [seekSeconds, seekNonce, segments, scrollToSegment])

  const handleSegmentClick = useCallback(
    (index: number) => {
      setActiveSegment(index)
      scrollToSegment(index, 'smooth')
    },
    [scrollToSegment]
  )

  return (
    <div className="transcript-viewer flex h-full w-full min-h-0 flex-col rounded-md border bg-white p-4 shadow-sm">
      <h2 className="mb-4 text-lg font-medium">转写结果</h2>
      {segments.length === 0 ? (
        <div className="flex h-full items-center justify-center text-muted-foreground">暂无转写内容</div>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-[80px_1fr] gap-2 border-b pb-2 text-xs font-medium text-muted-foreground">
            <div>时间</div>
            <div>内容</div>
          </div>
          <ScrollArea className="min-h-0 w-full flex-1">
            <div className="space-y-1">
              {segments.map((segment, index) => (
                <div
                  key={index}
                  ref={el => {
                    segmentRefs.current[index] = el
                  }}
                  className={cn(
                    'group grid grid-cols-[80px_1fr] gap-2 rounded-md p-2 transition-colors hover:bg-slate-50',
                    activeSegment === index && 'bg-slate-100'
                  )}
                  onClick={() => handleSegmentClick(index)}
                >
                  <div className="flex items-center gap-1 text-xs text-slate-500">
                    <span>{formatTime(segment.start)}</span>
                  </div>

                  <div className="text-sm leading-relaxed text-slate-700">
                    {segment.speaker && (
                      <span className="mr-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs font-medium text-slate-700">
                        {segment.speaker}
                      </span>
                    )}
                    {segment.text}
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </>
      )}

      {segments.length > 0 && (
        <div className="mt-4 flex justify-between border-t pt-3 text-xs text-slate-500">
          <span>共 {segments.length} 条片段</span>
          <span>总时长 {formatTime(durationSeconds)}</span>
        </div>
      )}
    </div>
  )
}

export default TranscriptViewer
