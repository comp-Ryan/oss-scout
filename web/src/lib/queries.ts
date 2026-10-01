import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from './api'

export type QueueParams = { lane?: string; category?: string; state?: string; difficulty?: string; sort?: string; all?: boolean; limit?: number; offset?: number }

export const useStatus = () => useQuery({ queryKey: ['status'], queryFn: api.getStatus, refetchInterval: 60000 })
export const useQueue = (p: QueueParams) => useQuery({ queryKey: ['queue', p], queryFn: () => api.getQueue(p), placeholderData: prev => prev })
export const useIssue = (id: string | null) => useQuery({ queryKey: ['issue', id], queryFn: () => api.getIssue(id!), enabled: !!id })
export const useBoard = () => useQuery({ queryKey: ['board'], queryFn: api.getBoard })
export const useRuns = () => useQuery({ queryKey: ['runs'], queryFn: api.getRuns, refetchInterval: 10000 })
export const useSettings = () => useQuery({ queryKey: ['settings'], queryFn: api.getSettings })

function useInvalidating<TArgs extends unknown[], TOut>(fn: (...a: TArgs) => Promise<TOut>, keys: string[][] = [['queue'], ['issue'], ['board'], ['status'], ['runs']]) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (args: TArgs) => fn(...args), onSuccess: () => keys.forEach(k => qc.invalidateQueries({ queryKey: k })) })
}
export const useAnalyze = () => useInvalidating((id: string) => api.postAnalyze(id))
export const useDismiss = () => useInvalidating((id: string, note: string) => api.postDismiss(id, note))
export const useDiscuss = () => useInvalidating((id: string) => api.postDiscuss(id))
export const useApprove = () => useInvalidating((id: string, plan: string) => api.postApprove(id, plan))
export const useMove = () => useInvalidating((id: string, column: string, pr?: string) => api.postMove(id, column, pr))
export const usePrep = () => useInvalidating((id: string) => api.postPrep(id))
export const useLaunch = () => useMutation({ mutationFn: (id: string) => api.postLaunch(id) })
export const useSubmit = () => useInvalidating((id: string, title: string, body: string) => api.postSubmit(id, title, body))
export const useSendMessage = () => useInvalidating((id: string, content: string) => api.postMessage(id, content), [['issue']])
export const useSaveSettings = () => useInvalidating((s: Parameters<typeof api.putSettings>[0]) => api.putSettings(s))
