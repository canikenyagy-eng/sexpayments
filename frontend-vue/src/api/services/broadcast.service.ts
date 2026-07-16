import api from '@/api/client'

export type BroadcastAudience = 'all' | 'except_blocked'
export type BroadcastStatus = 'pending' | 'sending' | 'done' | 'failed'

export interface Broadcast {
  id: number
  text: string
  audience: BroadcastAudience
  status: BroadcastStatus
  total_recipients: number
  delivered: number
  failed: number
  created_at: string
  finished_at: string | null
}

export interface RecipientCount {
  all: number
  except_blocked: number
}

export const broadcastService = {
  send: (payload: { text: string; audience: BroadcastAudience }) =>
    api.post<Broadcast>('/api/v1/broadcast/', payload),

  history: () => api.get<Broadcast[]>('/api/v1/broadcast/'),

  recipientCount: () => api.get<RecipientCount>('/api/v1/broadcast/recipient-count'),
}
