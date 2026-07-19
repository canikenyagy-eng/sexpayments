# SexPayments technical base

## Цель

SexPayments использует техническую базу PrimePay как платежный двигатель, но получает самостоятельный визуальный слой, язык интерфейса и продуктовую подачу.

## Что сохраняем

- Backend, API-контракты, модели данных и бизнес-правила.
- Роли admin, support, merchant, trader и teamlead.
- Авторизацию, 2FA, impersonation и маршрутизацию после входа.
- Сервисы `frontend-vue/src/api/services`, store-логику, типы и demo fixtures.
- Ботов и интеграционные процессы, пока дизайн не требует отдельного UX-сценария.

## Что меняем

- Брендинг, логотипы, визуальные assets и landing.
- Общий layout: rail navigation, command bar, фон, ритм страниц.
- Shared UI: кнопки, карточки, формы, таблицы, badges, empty states.
- Кабинеты по ролям:
  - merchant: CFO dashboard для оборота, конверсии, API, споров и выплат.
  - trader: production terminal для сделок, реквизитов, лимитов, чеков и рейтинга.
  - teamlead: operations center для команды, SLA, просадок, рисков и выплат.
  - support: moderation console для проверок, очередей и доказательств.

## Правило разработки

Любая новая работа должна сохранять PrimePay engine и менять только SexPayments interface, если задача явно не требует изменения бизнес-логики.
