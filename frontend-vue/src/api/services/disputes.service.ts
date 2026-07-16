import api from '@/api/client'
import type {
  DisputeEvidenceItem,
  DisputeReason,
  DisputeResponse,
  DisputeResolution,
  DisputeStatus,
  PaymentMethod,
} from '@/types'

export const disputesService = {
  // ── Admin ──
  listAll: (params?: {
    skip?: number
    limit?: number
    id_search?: string
    trader_login?: string
    merchant_login?: string
    status?: DisputeStatus
    reason?: DisputeReason
    payment_method?: PaymentMethod
    amount_from?: number
    amount_to?: number
  }) => api.get<DisputeResponse[]>('/api/v1/disputes', { params }),

  // Admin opens a dispute on any order by UUID (multipart; files/links optional).
  create: (data: { order_uuid: string; reason: DisputeReason; files?: File[]; evidenceUrls?: string[] }) => {
    const fd = new FormData()
    fd.append('order_uuid', data.order_uuid)
    fd.append('reason', data.reason)
    for (const f of data.files ?? []) fd.append('attachments', f)
    for (const u of data.evidenceUrls ?? []) fd.append('evidence_urls', u)
    return api.post<DisputeResponse>('/api/v1/disputes', fd)
  },

  resolve: (id: number, data: DisputeResolution) =>
    api.post<DisputeResponse>(`/api/v1/disputes/${id}/resolve`, data),

  reject: (id: number, data: DisputeResolution) =>
    api.post<DisputeResponse>(`/api/v1/disputes/${id}/reject`, data),

  // Admin: list a dispute's evidence files (by numeric id). The file bytes are
  // fetched separately from ``evidenceUrl`` via the authenticated downloader.
  listEvidence: (id: number) =>
    api.get<DisputeEvidenceItem[]>(`/api/v1/disputes/${id}/evidence`),

  evidenceUrl: (id: number, receiptUuid: string) =>
    `/api/v1/disputes/${id}/evidence/${receiptUuid}`,

  // ── Trader ──
  listMy: (params?: {
    skip?: number
    limit?: number
    status?: DisputeStatus
    reason?: DisputeReason
    payment_method?: PaymentMethod
    id_search?: string
    amount_from?: number
    amount_to?: number
  }) => api.get<DisputeResponse[]>('/api/v1/disputes/my', { params }),

  getMy: (uuid: string) =>
    api.get<DisputeResponse>(`/api/v1/disputes/my/${uuid}`),

  // Trader concedes → dispute resolved in the merchant's favour (order SUCCESS).
  acceptMy: (uuid: string) =>
    api.post<DisputeResponse>(`/api/v1/disputes/my/${uuid}/accept`),

  // Trader declines → dispute REJECTED in the trader's own favour (order FAILED).
  rejectMy: (uuid: string) =>
    api.post<DisputeResponse>(`/api/v1/disputes/my/${uuid}/reject`),

  // Trader asks the merchant for stronger proof (video / pdf). The dispute stays
  // OPEN (substatus video/pdf requested) and the merchant is nudged for it.
  requestProofMy: (uuid: string, kind: 'video' | 'pdf') =>
    api.post<DisputeResponse>(`/api/v1/disputes/my/${uuid}/request-proof`, null, {
      params: { kind },
    }),

  // Trader: list own dispute's evidence (premoderation-visible files only).
  listMyEvidence: (uuid: string) =>
    api.get<DisputeEvidenceItem[]>(`/api/v1/disputes/my/${uuid}/evidence`),

  myEvidenceUrl: (uuid: string, receiptUuid: string) =>
    `/api/v1/disputes/my/${uuid}/evidence/${receiptUuid}`,
}
