import api from '@/api/client'
import type { AuditLog, MerchantApiLog, OrderCreationSnapshot } from '@/types'

export const auditService = {
  getByEntity: (entityType: string, entityId: string, params?: { skip?: number; limit?: number }) =>
    api.get<AuditLog[]>(`/api/v1/audit/${entityType}/${entityId}`, { params }),

  getApiLogs: (params?: { skip?: number; limit?: number; merchant_id?: number; method?: string; endpoint_group?: string; sort_by?: string; sort_order?: string }) =>
    api.get<MerchantApiLog[]>('/api/v1/audit/api-logs', { params }),

  getSnapshot: (requestId: string) =>
    api.get<OrderCreationSnapshot | null>(`/api/v1/audit/api-logs/${requestId}/snapshot`),
}
