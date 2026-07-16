# Выбор провайдера проверки чеков трейдером

- **Дата:** 2026-07-03
- **Статус:** Утверждён (готов к плану реализации)
- **Область:** `backend-py` (модули `receipt_checks`, `traders`, orders API, celery-таска авто-проверки) + `frontend-vue` (модалка проверки чека, профиль трейдера, админ-панель провайдеров)

## Резюме

Трейдер выбирает, **каким** провайдером проверки чеков выполнить проверку, перед запуском.

- В модалке «Проверить чек» — радио-список активных провайдеров с **названием и ценой**.
- В профиле трейдера — выбор **провайдера по умолчанию**, который предвыбирается в модалке.
- Флаг `is_active` провайдера переосмысляется как **«доступен трейдерам для выбора»**: активными могут быть несколько провайдеров одновременно; неактивные не показываются трейдеру и не могут быть использованы через API.

## Текущее состояние (что меняем)

- `ReceiptCheckProvider` (`backend-py/app/modules/receipt_checks/models.py:19`) уже имеет `name`, `price_usdt`, `is_active`, `code`. Но сервис enforce-ит **ровно один активный** провайдер: `create_provider`/`update_provider` вызывают `deactivate_all()` (`service.py:113,150`); `run_check_for_order` берёт провайдера через `providers.get_active()` (`service.py:226`).
- Провайдер **не** выбирается трейдером — ни ручная проверка (`POST /api/v1/orders/{id}/receipt-check`, тело `{confirm}` — `ManualCheckRequest`, `schemas/admin.py:143`), ни авто-проверка (`workers/tasks/receipt_checks.py:84`).
- У `traders` нет колонки провайдера по умолчанию; есть только `receipt_auto_check: bool` (`traders/models.py:61`).
- Фронтенд: `ReceiptCheckModal.vue` тянет один `getQuote()` и показывает одну цену; `ProfileView.vue:65` имеет карточку «Проверка чеков» (только трейдер: цена + переключатель авто-проверки); админка `ReceiptCheckSettingsPanel.vue:137` пишет «Сделать активным (другие будут отключены)».
- В БД нет constraint «один активный» — только индекс `ix_receipt_check_providers_active` (миграция 030). Значит убрать `deactivate_all()` безопасно на уровне схемы.

## Цели / вне области

**Цели:** выбор провайдера в модалке; дефолт в профиле; мульти-актив; авто-проверка использует дефолт трейдера; неактивные провайдеры недоступны трейдеру (UI + API).

**Вне области:** редизайн админ-CRUD провайдеров (только смена подписи + снятие single-active); изменение логики списания/рефанда (цена уже берётся из объекта провайдера); i18n (в приложении его нет — строки хардкодятся по-русски, как везде).

## Дизайн — бэкенд

### 1. Мульти-актив (снятие single-active)

- Убрать вызовы `deactivate_all()` в `ReceiptCheckService.create_provider` и `update_provider`. Метод `deactivate_all` в репозитории оставить неиспользуемым можно удалить (проверить отсутствие других вызовов — их нет).
- Добавить `ReceiptCheckProviderRepository.list_active() -> list[ReceiptCheckProvider]` — все строки `is_active=True`, `ORDER BY id ASC`.
- `get_active()` оставить (используется в `platform_settings` «активный провайдер» и как совместимость), но на горячем пути проверки он больше не единственный источник.

### 2. Дефолт на трейдера (новая колонка)

- Новая колонка в `traders`: `default_receipt_check_provider_id = Column(Integer, ForeignKey("receipt_check_providers.id", ondelete="SET NULL"), nullable=True)`.
- Миграция Alembic **060** (следующая после 059), идемпотентная (проверка `_has_column`), по образцу 030. `SET NULL` → удаление провайдера очищает дефолт трейдера.
- `downgrade()` дропает колонку.

### 3. Резолвинг провайдера (одно центральное правило)

Новый метод `ReceiptCheckService.resolve_provider_for_trader(trader: Trader, requested_id: Optional[int]) -> Optional[ReceiptCheckProvider]`:

1. `requested_id` задан → загрузить провайдера; если не найден или `is_active=False` → `ValidationException("Selected receipt-check provider is not available")`. (Блокирует использование неактивных через API.)
2. иначе → `trader.default_receipt_check_provider_id`, **если провайдер существует и активен** → его.
3. иначе → **первый активный** (`list_active()[0]`).
4. активных нет → `None` (вызывающий отдаёт существующий `ConflictException` «нет активного провайдера»).

`run_check_for_order` получает новый **обязательный** параметр `provider: ReceiptCheckProvider` (вместо внутреннего `get_active()`). Тело метода не меняется по логике списания — `price = provider.price_usdt`, `ReceiptCheck.provider_id = provider.id`. Метод остаётся детерминированным/тестируемым; резолвинг вынесен к вызывающему.

### 4. API — ручная проверка

- `ManualCheckRequest` (`schemas/admin.py:143`): добавить `provider_id: Optional[int] = None`.
- Эндпоинт `run_receipt_check` (`orders.py:411`): после загрузки ордера — получить `Trader` текущего пользователя (через `TraderService.get_or_create_trader` или репозиторий), вызвать `resolve_provider_for_trader(trader, data.provider_id)`; если `None` → тот же нейтральный `ConflictException("Сервис проверки временно недоступен")`; иначе передать `provider` в `run_check_for_order`.
- `ValidationException` из резолвинга должен показать трейдеру понятную ошибку — добавить сообщение в allow-list `_TRADER_VISIBLE_RECEIPT_ERRORS` (`orders.py:463`): `"Selected receipt-check provider is not available": "Выбранный провайдер недоступен"`.

### 5. API — авто-проверка

- В `workers/tasks/receipt_checks.py`: после загрузки `trader_profile` вызвать `service.resolve_provider_for_trader(trader_profile, requested_id=None)` (→ дефолт трейдера, иначе первый активный); если `None` → best-effort skip (как сейчас при «нет провайдера»); иначе передать `provider` в `run_check_for_order`.

### 6. API — список провайдеров для трейдера (новый эндпоинт)

- Новая схема `TraderReceiptProviderResponse` в `receipt_checks/schemas/trader.py`: **только** `{ id: int, name: str, price_usdt: Decimal }`. Не отдаёт `code`/`adapter_type`/`base_url`/`api_key`/`settings`.
- Новый эндпоинт `GET /api/v1/orders/receipt-check/providers` (в `orders.py`, `dependencies=[Depends(require_trader)]`) → `list[TraderReceiptProviderResponse]` из `list_active()`. Пустой список = сервис недоступен.
- **Удалить** устаревший `GET /api/v1/orders/receipt-check/quote` (`orders.py:477`) и `get_receipt_check_quote` — он возвращал одного провайдера и полностью замещается списком. (Проверить прочих потребителей — только фронтовый `getQuote`, который тоже удаляем.)

### 7. Профиль трейдера — чтение/запись дефолта

- `TraderMeResponse` (`traders/schemas/trader.py:31`): добавить `default_receipt_check_provider_id: Optional[int] = None`. **Важно:** `transform_methods_config` строит явный dict (строки 73-83) — добавить туда `"default_receipt_check_provider_id": getattr(data, "default_receipt_check_provider_id", None)`.
- Новый запрос-схема `TraderDefaultProviderRequest { provider_id: Optional[int] }` (рядом с `TraderReceiptAutoCheckRequest`).
- Новый эндпоинт `PATCH /api/v1/traders/me/receipt-check-provider` (`traders.py`, после `toggle_receipt_auto_check`) → `TraderService.set_default_receipt_provider(user_id, provider_id)`.
- Новый метод `TraderService.set_default_receipt_provider(user_id, provider_id)` по образцу `toggle_receipt_auto_check` (`traders/service.py:79`): валидирует, что `provider_id` — активный провайдер (или `None` для сброса), пишет `audit_log` (old/new), обновляет колонку. Валидацию активности выполнить через `ReceiptCheckService`/репозиторий провайдеров (или прямой запрос).

### 8. Админ-панель провайдеров

- Никаких изменений схем/эндпоинтов, кроме снятия `deactivate_all` (см. п.1). Смысл «активен» = «доступен трейдерам».

## Дизайн — фронтенд

### Новый общий компонент

`frontend-vue/src/components/receipt-check/ReceiptProviderRadioGroup.vue` — радио-список: строки `provider.name` + `Number(price_usdt).toFixed(2) + ' USDT'`, `v-model` на `provider_id: number | null`. Используется и модалкой, и профилем (DRY, консистентный вид). Нативный `input[type=radio]`, стилизованный Tailwind в стиле проекта (референс — `CascadeProvidersView.vue`).

### API-сервисы (TS)

- `receiptChecks.service.ts`:
  - Добавить `listProviders(): Promise<{ id; name; price_usdt }[]>` → `GET /api/v1/orders/receipt-check/providers`.
  - `runManual(orderId, providerId?: number)` → тело `{ confirm: true, provider_id: providerId ?? null }`.
  - **Удалить** `getQuote()`.
- `traders.service.ts`: добавить `setDefaultReceiptProvider(providerId: number | null)` → `PATCH /api/v1/traders/me/receipt-check-provider` с телом `{ provider_id }`.

### Типы (TS)

- `Trader`: добавить `default_receipt_check_provider_id?: number | null`.
- Новый тип `TraderReceiptProvider { id: number; name: string; price_usdt: number }`.
- **Удалить** `ReceiptCheckQuote` (и его использования).

### Модалка `ReceiptCheckModal.vue`

- При открытии (watch `modelValue`) вместо `getQuote()` тянуть `listProviders()`; хранить `providers` и `selectedProviderId`.
- Начальный выбор: дефолт трейдера (передаётся пропом из `ActiveOrdersView` — он уже грузит профиль? если нет, тянуть через `tradersService.getMe()` при открытии), иначе первый активный (`providers[0]?.id`).
- В шаге `confirm` (когда **не** `alreadyChecked`) рендерить `<ReceiptProviderRadioGroup v-model="selectedProviderId" :providers="providers" />`; строка «будет списано» показывает цену выбранного провайдера.
- Пустой список → блок «Сервис проверки временно недоступен», кнопка выключена.
- `run()` → `receiptChecksService.runManual(orderUuid, selectedProviderId)`.
- Путь `alreadyChecked`/кэш не меняется (без радио, бесплатный повтор).

### Профиль `ProfileView.vue`

- В карточке «Проверка чеков» (только трейдер, `:65`) под переключателем авто-проверки — блок «Провайдер по умолчанию» с `ReceiptProviderRadioGroup`.
- `loadReceiptCheckBlock()` (`:157`): `Promise.all` дополнить `receiptChecksService.listProviders()`; заменить использование `getQuote()`. Инициализировать выбор из `traderProfile.default_receipt_check_provider_id`.
- Сохранение при изменении (как переключатель): `tradersService.setDefaultReceiptProvider(id)`, обновить `traderProfile`, тост.
- Нет активных провайдеров → показывать «Сервис проверки временно недоступен», селектор скрыт/выключен.
- Цену «одной проверки» теперь показываем из выбранного/дефолтного провайдера (или убрать одиночную строку цены, т.к. цена видна в радио-списке).

## Данные — сводка изменений

| Таблица | Изменение |
|---|---|
| `traders` | + `default_receipt_check_provider_id INT NULL FK → receipt_check_providers(id) ON DELETE SET NULL` |
| `receipt_check_providers` | без изменений схемы (меняется только сервис-логика single-active) |
| `receipt_checks` | без изменений (`provider_id` уже пишется) |

## Крайние случаи

- **Дефолт трейдера стал неактивным/удалён:** в модалке предвыбирается первый активный; в профиле неактивный не отображается; при следующем сохранении трейдер выберет валидный. Удаление провайдера → `SET NULL`.
- **Ноль активных провайдеров:** модалка и профиль показывают «Сервис недоступен»; ручная проверка → нейтральный конфликт; авто-проверка → best-effort skip.
- **Трейдер шлёт `provider_id` неактивного/несуществующего:** `ValidationException` → «Выбранный провайдер недоступен».
- **Кэш/уже проверено:** провайдер не нужен, списания нет — путь без радио, без изменений.
- **Гонки/дедуп ручной+авто:** без изменений (partial unique index по `(order_id, file_sha256)` сохраняется).

## Тестирование

**Юнит (сервис), `backend-py/tests`:**
- `resolve_provider_for_trader`: явный активный → он; явный неактивный/несуществующий → `ValidationException`; дефолт-активный → он; дефолт-неактивный → первый активный; дефолт `None` → первый активный; активных нет → `None`.
- `create_provider`/`update_provider`: активация НЕ деактивирует другие (мульти-актив).

**Интеграция / e2e:**
- `POST /orders/{id}/receipt-check` с `provider_id` активного → используется он (проверка `ReceiptCheck.provider_id` и списанной цены); с неактивным → 400/понятная ошибка; без `provider_id` при заданном дефолте → дефолт.
- `PATCH /traders/me/receipt-check-provider` → сохраняет; `GET /traders/me` возвращает поле; сброс `null`.
- `GET /orders/receipt-check/providers` возвращает только активных и только `{id,name,price_usdt}`.
- Админ-CRUD: два провайдера активны одновременно.

## Затрагиваемые файлы (ориентир)

**Backend:** `modules/receipt_checks/{service,repository}.py`, `modules/receipt_checks/schemas/{admin,trader}.py`, `modules/traders/{models,service}.py`, `modules/traders/schemas/trader.py` (+ `schemas/__init__` экспорт), `api/v1/endpoints/orders.py`, `api/v1/endpoints/traders.py`, `workers/tasks/receipt_checks.py`, новая миграция `versions/060_*.py`.

**Frontend:** `components/receipt-check/ReceiptProviderRadioGroup.vue` (новый), `components/receipt-check/ReceiptCheckModal.vue`, `views/shared/ProfileView.vue`, `views/trader/ActiveOrdersView.vue` (проп дефолта, если нужно), `api/services/receiptChecks.service.ts`, `api/services/traders.service.ts`, `components/platform-settings/ReceiptCheckSettingsPanel.vue` (подпись), `types/index.ts`.
