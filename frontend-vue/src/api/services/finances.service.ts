import api from '@/api/client'
import type {
  AdminAdjustRequest,
  AdminAdjustResponse,
  BalanceInfo,
  HashDepositPreview,
  HashDepositResult,
  LedgerEntry,
  TraderFinanceStats,
  UserRole,
  WithdrawalRequest,
  WithdrawalStatus,
} from '@/types'

export const financesService = {
  listWithdrawals: (params?: {
    skip?: number
    limit?: number
    status?: WithdrawalStatus
    user_role?: UserRole
    user_login?: string
  }) => api.get<WithdrawalRequest[]>('/api/v1/finances/withdrawals', { params }),

  listMyWithdrawals: (params?: { skip?: number; limit?: number; status?: WithdrawalStatus }) =>
    api.get<WithdrawalRequest[]>('/api/v1/finances/my-withdrawals', { params }),

  createMyWithdrawal: (data: { amount: number; currency: string; destination_address: string }) =>
    api.post<WithdrawalRequest>('/api/v1/finances/my-withdrawals', data),

  approveWithdrawal: (id: number) =>
    api.post<WithdrawalRequest>(`/api/v1/finances/withdrawals/${id}/approve`),

  rejectWithdrawal: (id: number, reason: string) =>
    api.post<WithdrawalRequest>(`/api/v1/finances/withdrawals/${id}/reject`, { reason }),

  listBalances: (params?: {
    skip?: number
    limit?: number
    user_id?: number
    merchant_id?: number
  }) => api.get<BalanceInfo[]>('/api/v1/finances/balances', { params }),

  listMyBalances: () =>
    api.get<BalanceInfo[]>('/api/v1/finances/my-balances'),

  listLedger: (params?: {
    skip?: number
    limit?: number
    reference_type?: string
    order_search?: string
    user_login?: string
    amount_from?: number
    amount_to?: number
  }) => api.get<LedgerEntry[]>('/api/v1/finances/ledger', { params }),

  getMyStats: (params?: {
    date_from?: string
    date_to?: string
  }) => api.get<TraderFinanceStats>('/api/v1/finances/my-stats', { params }),

  listMyLedger: (params?: {
    skip?: number
    limit?: number
    reference_type?: string
    order_search?: string
    amount_from?: number
    amount_to?: number
  }) => api.get<LedgerEntry[]>('/api/v1/finances/my-ledger', { params }),

  adminAdjust: (data: AdminAdjustRequest) =>
    api.post<AdminAdjustResponse>('/api/v1/finances/admin/adjust', data),

  // Top-up by TRC20 tx hash — two-phase: verify (preview, no credit) then confirm.
  verifyHashDeposit: (data: { user_id: number; tx_hash: string }) =>
    api.post<HashDepositPreview>('/api/v1/finances/admin/hash-deposit/verify', data),

  confirmHashDeposit: (data: { user_id: number; tx_hash: string }) =>
    api.post<HashDepositResult>('/api/v1/finances/admin/hash-deposit/confirm', data),
}
