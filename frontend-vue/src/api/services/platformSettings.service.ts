import api from '@/api/client'
import type {
  AchievementSettings,
  AchievementSettingsUpdate,
  DolivSettings,
  DolivSettingsUpdate,
  NotificationSettings,
  NotificationSettingsUpdate,
  PremoderationSettings,
  PremoderationSettingsUpdate,
  PrimeTimeActivate,
  PrimeTimeState,
  ReceiptCheckProvider,
  ReceiptCheckProviderBalance,
  ReceiptCheckProviderCreate,
  ReceiptCheckProviderUpdate,
} from '@/types'

/**
 * Admin-side calls for /api/v1/platform-settings.
 *
 * The receipt-check tab is the only consumer right now; the file is named
 * generically (`platformSettings`) so future tabs (KYC, integrations, …)
 * can slot in without renaming.
 */
export const platformSettingsService = {
  // ── Receipt-check providers ─────────────────────────
  listReceiptCheckProviders: () =>
    api.get<ReceiptCheckProvider[]>('/api/v1/platform-settings/receipt-check/providers'),

  getActiveReceiptCheckProvider: () =>
    api.get<ReceiptCheckProvider>('/api/v1/platform-settings/receipt-check/providers/active'),

  createReceiptCheckProvider: (data: ReceiptCheckProviderCreate) =>
    api.post<ReceiptCheckProvider>('/api/v1/platform-settings/receipt-check/providers', data),

  updateReceiptCheckProvider: (id: number, data: ReceiptCheckProviderUpdate) =>
    api.patch<ReceiptCheckProvider>(`/api/v1/platform-settings/receipt-check/providers/${id}`, data),

  deleteReceiptCheckProvider: (id: number) =>
    api.delete<void>(`/api/v1/platform-settings/receipt-check/providers/${id}`),

  getReceiptCheckProviderBalance: (id: number) =>
    api.get<ReceiptCheckProviderBalance>(
      `/api/v1/platform-settings/receipt-check/providers/${id}/balance`,
    ),

  // ── Receipt premoderation (support-bot) ─────────────
  getPremoderationSettings: () =>
    api.get<PremoderationSettings>('/api/v1/platform-settings/premoderation'),

  updatePremoderationSettings: (data: PremoderationSettingsUpdate) =>
    api.patch<PremoderationSettings>('/api/v1/platform-settings/premoderation', data),

  // ── Platform notifications (support-bot) ────────────
  getNotificationSettings: () =>
    api.get<NotificationSettings>('/api/v1/platform-settings/notifications'),

  updateNotificationSettings: (data: NotificationSettingsUpdate) =>
    api.patch<NotificationSettings>('/api/v1/platform-settings/notifications', data),

  // ── Долив (requisite refill) ────────────────────────
  getDolivSettings: () =>
    api.get<DolivSettings>('/api/v1/platform-settings/doliv'),

  updateDolivSettings: (data: DolivSettingsUpdate) =>
    api.patch<DolivSettings>('/api/v1/platform-settings/doliv', data),

  // ── Prime-Time (global trader-fee boost) ────────────
  getPrimeTime: () =>
    api.get<PrimeTimeState>('/api/v1/platform-settings/primetime'),

  activatePrimeTime: (data: PrimeTimeActivate) =>
    api.post<PrimeTimeState>('/api/v1/platform-settings/primetime', data),

  stopPrimeTime: () =>
    api.delete<void>('/api/v1/platform-settings/primetime'),

  // ── Достижения / бонусы трейдеров ───────────────────
  getAchievementSettings: () =>
    api.get<AchievementSettings>('/api/v1/platform-settings/achievements'),

  updateAchievementSettings: (data: AchievementSettingsUpdate) =>
    api.patch<AchievementSettings>('/api/v1/platform-settings/achievements', data),
}
