# Cascade provider integrations

Каждый файл в этой папке — один внешний провайдер реквизитов. Чтобы добавить
нового, **создаёшь один файл** — больше ничего редактировать не нужно:
registry автоматически найдёт `ProviderAdapter`-подкласс при импорте, фронт
сам отрисует форму настроек из вашей `SETTINGS_SCHEMA`, HTTP-транспорт,
подпись запросов, верификация webhook'ов и дешифровка credentials живут в
базовом классе.

Текущие адаптеры в репо: [`mock.py`](mock.py),
[`legacy_crypto.py`](legacy_crypto.py), [`swifty.py`](swifty.py),
[`bridgepay.py`](bridgepay.py). Смотри их как живые примеры — каждый
демонстрирует свою auth-схему.

---

## Чек-лист «добавить нового провайдера»

1. Прочитай раздел [Как написать адаптер](#как-написать-адаптер).
2. Положи файл `app/modules/cascading/integrations/<name>.py`.
3. Подкласс `ProviderAdapter` с **обязательной** `code` (короткое машинное имя).
4. Заполни `SETTINGS_SCHEMA` — фронт нарисует форму без правок.
5. Реализуй 6 abstract методов через base helpers (`self.request_signed`,
   `self.safe_request`, `self.upload_file`, …).
6. Опционально override класс-аттрибуты для нестандартного auth/подписи.
7. Опционально реализуй `get_balance` и `poll_status`.
8. Скопируй [`tests/unit/test_provider_adapter_swifty.py`](../../../../tests/unit/test_provider_adapter_swifty.py)
   как шаблон, поменяй `SwiftyAdapter` → ваш класс.
9. `docker compose build backend && docker compose up -d backend` — адаптер
   появится в админ-дропдауне сразу.

Никаких правок `registry.py`, `service.py`, фронта, миграций.

---

## Контракт `ProviderAdapter`

### Обязательные class-атрибуты

| Атрибут | Тип | Описание |
|---|---|---|
| `code` | `str` | Уникальный машинный код, сохраняется в `CascadeProvider.adapter_type`. **Не пустой.** |

### Декларации (опционально, но почти всегда хочешь)

| Атрибут | Тип | Что |
|---|---|---|
| `display_name` | `str` | Имя в дропдауне админки. По умолчанию = `code`. |
| `description` | `str` | Подсказка под дропдауном — 1–2 предложения. |
| `supports_provider_rate` | `bool` | Может ли провайдер прислать свой курс в ответе. `False` → `rate_source='provider'` отключается в UI и принудительно `platform` + RateConfig. |
| `SUPPORTED_METHODS` | `tuple[PaymentMethod, ...]` | Какие методы поддерживает провайдер. Cascade scheduler фильтрует. |
| `PROVIDER_STATUS_MAP` | `dict[str, ProviderStatus]` | Их статус-строка → наш `ProviderStatus`. Используется `parse_provider_status`. |
| `SETTINGS_SCHEMA` | `tuple[AdapterFieldSpec, ...]` | Декларативная схема формы. См. [SETTINGS_SCHEMA](#settings_schema-—-форма-настроек). |
| `HTTP_STATUS_TO_REFUSAL` | `dict[int, str]` | Доп. маппинги HTTP-кода → refusal-code для `refusal_from_response`. Дефолт уже покрывает 400/401/403/404/409/413/422/429. |
| `DEFAULT_METHOD_MAP` | `dict[str, str]` | Наш `PaymentMethod.value` → метод-string провайдера (sbp/c2c/SBP/TO_CARD/...). Используется `resolve_method_value` / дефолтным `supports`. |
| `METHOD_MAP_SETTING_KEY` | `"method_map"` | Имя ключа в `provider.settings`, который при заполнении работает как closed whitelist поверх `DEFAULT_METHOD_MAP`. Для нестандартных названий: `"preferred_method_map"` (legacy), `"payment_option_map"` (bridgepay), `"method_code_map"` (swifty). |
| `PROVIDER_METHOD_TO_OURS` | `dict[str, PaymentMethod]` | Reverse-map для `resolve_payment_method_from`: что вернул провайдер в ответе → наш `PaymentMethod`. Покрывает их более широкий vocabulary (cross-border, bank-to-bank). |
| `BANK_CODE_TRANSFORM` | `"upper"` | Что делать с `PaymentOption.code` когда нет override в `BANK_MAP_SETTING_KEY`: `"upper"` (SBER), `"lower"` (sber), `"identity"` (sber). |
| `BANK_MAP_SETTING_KEY` | `"bank_code_map"` | Ключ в `provider.settings` для override маппинга банков. |
| `REQUIRED_SETTINGS` | `()` | Список ключей в `provider.settings`, которые обязаны быть непустыми. Базовая `validate_provider_config` короткозамыкается с `ProviderRefusal("misconfigured")` при отсутствии. |
| `REQUIRED_CREDENTIALS` | `()` | Аналогично, но для зашифрованных колонок провайдера. Значения: `"api_secret"`, `"api_key"`, `"webhook_secret"`. |

### Signing knobs (class-атрибуты)

Меняй только когда default не подходит. Default = LegacyCrypto-style:
`HMAC-SHA256(secret, "{ts}.{METHOD}.{path}{canonical_json}")` в `X-Signature`,
`Authorization: Bearer <secret>`.

| Атрибут | Default | Зачем менять |
|---|---|---|
| `SIGNATURE_ALGORITHM` | `"sha256"` | Провайдер требует `sha1` / `sha512`. (`bridgepay.py`: `sha1`.) |
| `SIGNATURE_ENCODING` | `"hex"` | Провайдер ждёт `base64`. (`bridgepay.py`: `base64`.) |
| `AUTH_HEADER` | `"Authorization"` | Нестандартный header. (`swifty.py`: `X-Secret`. `bridgepay.py`: `X-Identity`. `bitzone.py`: `x-api-key`.) `""` — не отправлять auth-заголовок. |
| `AUTH_SCHEME` | `"Bearer"` | Bare token (`""`), `Basic`, etc. (`swifty.py` / `bridgepay.py` / `bitzone.py`: `""`.) |
| `TIMESTAMP_HEADER` | `"X-Timestamp"` | Другое имя или `""` если timestamp не нужен. |
| `SIGNATURE_HEADER` | `"X-Signature"` | Другое имя. (`swifty.py` использует это поле и для webhook X-Hash; `bitzone.py`: `x-signature`; `mock.py`: `X-Mock-Signature`.) |
| `IDEMPOTENCY_HEADER` | `"X-Idempotency-Key"` | Другое имя или `""` если провайдер не поддерживает idempotency-ключи. (`swifty.py` / `bitzone.py` / `payscrow.py`: `""` — идемпотентность по orderId/externalTransactionId в body.) |
| `SIGN_REQUESTS` | `True` | `False` → пропустить таймстемп+подпись на исходящих запросах. Только auth-заголовок (плюс idempotency, если задан). Используй для провайдеров, которые авторизуют по одному API-ключу. (`swifty.py` / `bitzone.py` / `garex.py` / `payscrow.py`: `False`.) |
| `WEBHOOK_SECRET_SOURCE` | `"webhook_secret"` | Откуда брать HMAC-секрет для верификации webhook'а: `"webhook_secret"` (default — `webhook_secret_encrypted`), `"api_key"` (провайдер шарит API-ключ для webhook — `bitzone.py`), `"api_secret"` (тот же секрет, что для подписи исходящих — `payscrow.py`). |
| `WEBHOOK_TOKEN_HEADER` | `""` | Если задан, базовый `verify_callback_signature` переключается в **token-compare** режим: просто сверяет header с `webhook_signing_secret` байт-в-байт, без HMAC. Для провайдеров, которые пересылают известный токен в webhook. (`bridgepay.py`: `X-Notification-Token`. `payscrow.py`: `X-API-Key`.) |
| `EXTRA_AUTH_HEADER` | `""` | Второй auth-заголовок, который добавляется к каждому запросу. Значение берётся из `provider.api_key` автоматически. Используется, когда провайдер требует **два независимых credential'а** в headers одновременно. (`bitwire.py`: `X-Api-Key` рядом с `Authorization: Bearer <JWT>`.) |

Когда менять не атрибут, а сам метод — override:

| Метод | Когда |
|---|---|
| `build_signature_payload(timestamp, method, path, body_json)` | Provider expects `"METHOD\nPATH\nTIMESTAMP\nBODY"` или другой layout. |
| `serialize_request_body(body)` | Provider signs *raw* JSON (BridgePay) или другой формат — нужно гарантировать «байты подписи == байты транспорта». Default — `canonical_json` (sort_keys + no whitespace). |
| `sign_request(token, method, path, body, idempotency_key, extra_headers, provider)` | Полностью нестандартный auth — dual-credential (BridgePay), отдельный X-Identity + X-Signature, signing full URL вместо path, и т. п. `provider` доступен для доступа к `provider.base_url` / `api_key_encrypted`. |
| `verify_callback_signature(secret, headers, body)` | Webhook подписан не от raw body — nested HMAC (Swifty), token compare (BridgePay), JWT, и т. п. |
| `async acquire_token(provider) -> str` | Возвращает primary credential (значение для `Authorization` header). Default — статический `get_token` (api_secret_encrypted). Override для **динамических токенов**: JWT с refresh-on-expiry (`bitwire.py`), OAuth2 client credentials grant, sessions. Async — можно дёргать auth endpoint и кэшировать. |

### Абстрактные / обязательные методы

Базовый класс реализует **Template Method**: `supports()` и
`issue_requisite()` уже есть в `ProviderAdapter`, они работают на основе
декларативных class-атрибутов выше. Адаптеру достаточно реализовать
два метода-парсера и обычные действия по управлению заявкой.

| Метод | Что делает | Обязателен? |
|---|---|---|
| `supports(provider, method, payment_option_code) -> bool` | Cheap pre-check. **Базовая реализация** проверяет `SUPPORTED_METHODS`, `validate_provider_config` и `resolve_method_value`. Override только если нужны нестандартные проверки. | Нет, есть default |
| `async issue_requisite(...)` | **Template Method** в базе: validate → coerce method → `build_payin_request` → `request_signed` → `parse_payin_response`. Override только при многоэтапной выдаче. | Нет, есть default |
| `build_payin_request(provider, order_data, idempotency_key, method, method_value) -> PayinRequest \| ProviderRefusal` | Возвращает `PayinRequest(http_method, path, body, ...)` — что и как послать. Может вернуть `ProviderRefusal` если детали заказа невозможно представить запросом. | **Да** (если не override `issue_requisite`) |
| `parse_payin_response(provider, resp, order_data, fallback_method) -> ProviderRequisiteResponse \| ProviderRefusal` | Парсинг `httpx.Response` → типизированный результат. Используй `parse_envelope_or_refusal()` для status_code + JSON checks. | **Да** |
| `async cancel_request(provider, external_order_id, timeout_ms) -> bool` | Отмена заявки. Best-effort через `safe_request`. | **Да** |
| `async notify_receipt(provider, external_order_id, receipt_path, comment) -> bool` | Передача чека от мерчанта. Multipart через `upload_file` или base64 через `file_to_data_url`. | **Да** |
| `async raise_dispute(provider, external_order_id, reason, evidence_paths) -> bool` | Эскалация спора. | **Да** |
| `parse_callback(provider, headers, body, query_params=None) -> ParsedCallback` | Парсинг webhook'а. Для POST-with-JSON используй `verify_and_decode_callback()`. Для **GET-callback'ов** (Bitwire: `GET ?id=X&status=Y`) body будет пустой, читай `query_params` напрямую. На bad signature — `raise CallbackVerificationError`. | **Да** |

### Опциональные методы

| Метод | Когда переопределять |
|---|---|
| `async poll_status(provider, external_order_id, timeout_ms) -> ParsedCallback \| None` | Fallback на случай потерянного webhook'а. Default — `None` (опрос отключён). Реализуй через `safe_request` GET и `try_parse_provider_status`. |
| `async get_balance(provider, timeout_ms=None) -> Decimal \| None` | Запрос баланса мерчантского счёта у провайдера. Admin UI покажет рядом с локальным балансом. Default — `None`. |

---

## Base helpers — используй вместо boilerplate

Все эти методы доступны через `self.<...>` внутри адаптера. Не реализуй
заново и не копируй из других адаптеров.

### Credentials и settings

```python
self.get_settings(provider)               # provider.settings or {} — null-safe dict
self.get_token(provider)                  # decrypted api_secret; raises CallbackVerificationError if missing
self.get_token(provider, required=False)  # тот же, но возвращает "" вместо raise
self.get_api_key(provider)                # decrypted api_key (Optional[str])
self.get_webhook_secret(provider)         # decrypted webhook_secret (Optional[str]) — None = "skip verification"
```

### HTTP

```python
# Главный путь — для issue_requisite. Поднимает RequestError, который cascade
# scheduler стампит как ERROR.
await self.request_signed(
    provider=provider, method="POST", path="/x", body={...},
    idempotency_key=..., timeout_ms=..., extra_headers=...,
    params=..., files=..., form_data=...,
)

# Для идемпотентных операций — cancel/poll/balance/dispute/receipt.
# Возвращает None при RequestError, логирует с adapter.code + log_event.
await self.safe_request(
    provider=provider, method="GET", path="/x",
    body=None, timeout_ms=..., log_event="poll_failed",
)

# Multipart upload — открывает файл, проверяет существование, закрывает.
await self.upload_file(
    provider=provider, method="POST", path="/x/dispute",
    file_path=receipt_path, file_field="attachment",
    body=None,  # multipart payload не подписывается body-частью
    form_data={"dealId": "..."},
    timeout_ms=..., log_event="dispute_failed",
)

# Multipart upload с НЕСКОЛЬКИМИ файлами под одним field name —
# для провайдеров типа Payscrow, где dispute принимает files[] массив.
# Открывает каждый файл, пропускает несуществующие, закрывает все handles.
await self.upload_files(
    provider=provider, method="POST", path="/api/v1/disputes/create",
    file_paths=[path1, path2], file_field="files",
    form_data={"order_id": "...", "amount": "..."},
    timeout_ms=..., log_event="dispute_failed",
)
```

### Подпись

```python
self.canonical_json(body)                   # sort_keys + no whitespace
self.serialize_request_body(body)           # ровно те байты, что httpx отправит
self.build_signature_payload(timestamp=..., method=..., path=..., body_json=...)
self.compute_signature(secret=..., payload=...)
self.sign_request(token=..., method=..., path=..., body=..., provider=...)
self.verify_callback_signature(secret=..., headers=..., body=...)
self.get_webhook_signing_secret(provider)   # резолвит секрет по WEBHOOK_SECRET_SOURCE
self.verify_and_decode_callback(            # короткий путь для parse_callback:
    provider=..., headers=..., body=...     #   verify (если секрет есть) + decode → dict
)
```

### Файлы

```python
self.file_to_data_url(file_path)             # путь → "data:<mime>;base64,..." или None
self.file_to_data_url(file_path, mime_type="application/pdf")  # явный MIME
```

Используй когда провайдер принимает файл inline в JSON-теле (Garex's PATCH
status), а не multipart. MIME подбирается по расширению (jpg/png/pdf/mp4/...).

### Декларативные резолверы (читают class-atrs)

```python
self.resolve_method_value(provider, method)  # наш PaymentMethod → их method-string
self.resolve_payment_method_from(            # их method-string → наш PaymentMethod (reverse)
    raw_value, default=fallback_method,
)
self.resolve_bank_code(provider, code)       # наш PaymentOption.code → их bank-string
self.validate_provider_config(provider)      # → Optional[ProviderRefusal("misconfigured")]
self.parse_envelope_or_refusal(              # status_code + JSON-декод → dict | ProviderRefusal
    resp, ok_statuses=(200, 201),
    bad_response_message="...",
)
```

Эти методы покрывают 80% boilerplate в адаптерах. Override напрямую если
нужна сложная логика (BridgePay's CARD default fallback — см. там override
`resolve_method_value`).

### Парсинг

```python
self.decode_callback_body(body)              # bytes → dict; raises CallbackVerificationError на bad JSON
self.json_or_none(resp)                      # resp.json() с проглатыванием ошибок
self.safe_decimal(value)                     # Decimal(str(v)) с None при ошибке
self.parse_iso8601(value)                    # "2024-11-04T13:55:45+00:00" → datetime(UTC)
self.coerce_payment_method(v, default=...)   # string/enum → PaymentMethod
self.format_amount(x)                        # Decimal → "10.00" без научной нотации
self.parse_provider_status(raw)              # их строка → ProviderStatus; raises на unknown
self.try_parse_provider_status(raw)          # то же, но None на miss — для poll_status
self.refusal_from_response(resp)             # 4xx httpx.Response → ProviderRefusal
self.default_expires_at(provider,
                        fallback_seconds=1800,
                        settings_key="expires_default_seconds")
```

---

## `SETTINGS_SCHEMA` — форма настроек

Декларативный список полей. Фронт ([`AdapterSettingsForm.vue`](../../../../../frontend-vue/src/components/cascade/AdapterSettingsForm.vue))
рендерит правильный input для каждого `type` без правок фронта.

```python
from app.modules.cascading.integrations.base import (
    AdapterFieldSpec, AdapterFieldOption,
)

SETTINGS_SCHEMA = (
    AdapterFieldSpec(
        key="merchant_id",
        label="Merchant ID",
        type="number",
        required=True,
        min=1,
        description="Идёт в URL запросов /payment, /balance, /check.",
    ),
    AdapterFieldSpec(
        key="callback_url",
        label="Callback URL",
        type="url",
        placeholder="https://us/api/cascade/v1/callbacks/<code>",
    ),
    AdapterFieldSpec(
        key="auto_capture",
        label="Auto-capture",
        type="boolean",
        default=True,
    ),
    AdapterFieldSpec(
        key="webhook_secret",
        label="Webhook secret",
        type="string",
        secret=True,  # input type=password в UI
    ),
    AdapterFieldSpec(
        key="preferred_method",
        label="Preferred method",
        type="select",
        options=[
            AdapterFieldOption("sbp", "СБП"),
            AdapterFieldOption("card", "Карта"),
        ],
    ),
    AdapterFieldSpec(
        key="supported_methods",
        label="Supported methods",
        type="multi_select",  # выбирай несколько, сохраняется как list
        options=[
            AdapterFieldOption("sbp", "СБП"),
            AdapterFieldOption("card", "Карта"),
            AdapterFieldOption("sim", "SIM"),
        ],
    ),
    AdapterFieldSpec(
        key="bank_code_map",
        label="Маппинг банков",
        type="kv_map",          # рендерится как набор (key, value) пар + кнопка "+"
        value_type="string",    # либо "select" с options для closed whitelist
        description="наш PaymentOption.code → их код банка",
    ),
)
```

Поддерживаемые `type`-значения:

| Тип | UI |
|---|---|
| `string` | text input (или `password` при `secret=True`) |
| `textarea` | многострочное поле |
| `url` | text input с подсказкой "URL" |
| `number` | number input, с `min`/`max` если заданы, дефолт из `default` |
| `boolean` | checkbox |
| `select` | dropdown с `options` |
| `multi_select` | набор чекбоксов, значение — массив выбранных |
| `kv_map` | пары "ключ → значение". `value_type="select"` делает значение dropdown'ом из `options` |

---

## Quick start: новый адаптер целиком

```python
"""ExampleProvider cascade adapter."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.modules.cascading.integrations.base import (
    AdapterFieldOption, AdapterFieldSpec, CallbackVerificationError,
    IssueResult, ParsedCallback, ProviderAdapter,
    ProviderRefusal, ProviderRequisiteResponse,
)
from app.modules.cascading.models import CascadeProvider


class ExampleAdapter(ProviderAdapter):
    code = "example"
    display_name = "Example Provider"
    description = "Описание для админки в 1-2 предложения."
    supports_provider_rate = True

    SUPPORTED_METHODS = (PaymentMethod.SBP, PaymentMethod.CARD)
    PROVIDER_STATUS_MAP = {
        "Active": ProviderStatus.PENDING,
        "Paid": ProviderStatus.PAID,
        "Done": ProviderStatus.SUCCESS,
        "Failed": ProviderStatus.FAILED,
        "Cancelled": ProviderStatus.CANCELED,
    }

    SETTINGS_SCHEMA = (
        AdapterFieldSpec(key="callback_url", label="Webhook URL", type="url"),
        AdapterFieldSpec(
            key="bank_code_map", label="Маппинг банков",
            type="kv_map", value_type="string",
        ),
    )

    def supports(self, *, provider, method, payment_option_code):
        return method in self.SUPPORTED_METHODS

    async def issue_requisite(self, *, provider, order_data, idempotency_key, timeout_ms):
        method = self.coerce_payment_method(order_data["payment_method"])
        body = {
            "amount": self.format_amount(order_data["amount"]),
            "currency": order_data["currency"].value,
            "method": method.value.upper(),
            "callback_url": self.get_settings(provider).get("callback_url"),
        }
        resp = await self.request_signed(
            provider=provider, method="POST", path="/v1/orders",
            body=body, idempotency_key=idempotency_key, timeout_ms=timeout_ms,
        )
        if resp.status_code != 200:
            return self.refusal_from_response(resp)

        data = (self.json_or_none(resp) or {}).get("data") or {}
        return ProviderRequisiteResponse(
            external_order_id=str(data["id"]),
            bank_name=data["bank"],
            account_number=data["account"],
            account_holder=data["holder"],
            payment_method=method,
            payment_option_code=order_data.get("payment_option_code"),
            amount_fiat=self.safe_decimal(data.get("amount")) or Decimal("0"),
            expires_at=self.parse_iso8601(data.get("expiresAt"))
                       or self.default_expires_at(provider),
            raw=data,
            provider_rate=self.safe_decimal(data.get("rate")),
        )

    async def cancel_request(self, *, provider, external_order_id, timeout_ms):
        resp = await self.safe_request(
            provider=provider, method="POST",
            path=f"/v1/orders/{external_order_id}/cancel",
            timeout_ms=timeout_ms, log_event="cancel_failed",
        )
        return resp is not None and resp.status_code in (200, 404)

    async def notify_receipt(self, *, provider, external_order_id, receipt_path, comment):
        if not receipt_path:
            return True
        resp = await self.upload_file(
            provider=provider, method="POST",
            path=f"/v1/orders/{external_order_id}/receipt",
            file_path=receipt_path, file_field="attachment",
            timeout_ms=provider.request_timeout_ms,
            log_event="receipt_failed",
        )
        return resp is not None and resp.status_code == 200

    async def raise_dispute(self, *, provider, external_order_id, reason, evidence_paths):
        ok = True
        for path in evidence_paths or [None]:
            resp = await self.upload_file(
                provider=provider, method="POST",
                path=f"/v1/orders/{external_order_id}/dispute",
                file_path=path, form_data={"reason": reason},
                timeout_ms=provider.request_timeout_ms,
                log_event="dispute_failed",
            )
            if resp is None or resp.status_code != 200:
                ok = False
        return ok

    def parse_callback(self, *, provider, headers, body):
        # verify_and_decode_callback: одна строка вместо трёх. Сам резолвит секрет
        # через WEBHOOK_SECRET_SOURCE, верифицирует, декодирует, возвращает dict.
        payload = self.verify_and_decode_callback(
            provider=provider, headers=headers, body=body,
        )
        external_id = payload.get("orderId")
        if not external_id:
            raise CallbackVerificationError("Missing orderId")
        return ParsedCallback(
            external_order_id=str(external_id),
            status=self.parse_provider_status(payload.get("status")),
            raw=payload,
            paid_amount_fiat=self.safe_decimal(payload.get("amount")),
        )

    async def poll_status(self, *, provider, external_order_id, timeout_ms):
        resp = await self.safe_request(
            provider=provider, method="GET",
            path=f"/v1/orders/{external_order_id}",
            timeout_ms=timeout_ms, log_event="poll_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        envelope = self.json_or_none(resp) or {}
        status = self.try_parse_provider_status(envelope.get("status"))
        if status is None:
            return None
        return ParsedCallback(
            external_order_id=str(envelope.get("id") or external_order_id),
            status=status, raw={"event": "polled", "envelope": envelope},
        )

    async def get_balance(self, *, provider, timeout_ms=None):
        resp = await self.safe_request(
            provider=provider, method="GET", path="/v1/balance",
            timeout_ms=timeout_ms, log_event="balance_failed",
        )
        if resp is None or resp.status_code != 200:
            return None
        return self.safe_decimal((self.json_or_none(resp) or {}).get("balance"))
```

Что **не** нужно делать:
- ❌ Открывать `httpx.AsyncClient` вручную.
- ❌ Писать `try: resp.json() except: ...`.
- ❌ Писать `try: Decimal(str(v)) except: ...`.
- ❌ Открывать файлы для multipart руками.
- ❌ Считать HMAC руками — `self.compute_signature(secret=..., payload=...)`.
- ❌ Дёргать `decrypt_api_secret` напрямую — `self.get_token / get_api_key / get_webhook_secret`.
- ❌ Локальные `_STATUS_MAP` константы — используй `PROVIDER_STATUS_MAP` class attr.
- ❌ Регистрировать адаптер в `registry.py` — auto-discovery подхватит.

---

## Тесты

Два уровня:

### Unit (моки) — `tests/unit/test_provider_adapter_*.py`

Бегут offline через testkit (`tests/unit/cascade_adapter_testkit.py`).
Покрывают парсинг/подписи/маппинги для всех адаптеров. Запуск:

```bash
PYTHONPATH=. pytest tests/unit/test_provider_adapter_<name>.py -v
```

### Полуавтоматический live-probe против реального API провайдера

CLI `python -m app.modules.cascading.cli` дёргает теже adapter-методы,
что использует cascade scheduler, но против настоящих эндпоинтов
провайдера. Кредентилы тянутся из той же `CascadeProvider` строки в
БД, что и в продакшене — никаких отдельных тест-кредов.

```bash
# Список провайдеров, которые видны этому DATABASE_URL
python -m app.modules.cascading.cli list

# Read-only ping (auth + connectivity + balance) — безопасно для cron / CI
python -m app.modules.cascading.cli ping legacy_crypto_main

# Только баланс — alias для финансового мониторинга
python -m app.modules.cascading.cli balance legacy_crypto_main

# Реальная выдача реквизита (НЕ read-only — заблокирует трейдера на их стороне)
python -m app.modules.cascading.cli issue legacy_crypto_main \
    --amount 1500 --method sbp --option-code sber

# Откатить заблокированный реквизит (external_order_id берёшь из вывода issue)
python -m app.modules.cascading.cli cancel legacy_crypto_main PI-abc123xyz

# Опросить статус известного order_id
python -m app.modules.cascading.cli poll legacy_crypto_main PI-abc123xyz

# End-to-end: ping → issue → cancel. Безопасно для CI/cron — даже при сбое
# адаптера реквизит на провайдере не зависнет: либо отменяется явно, либо
# истекает по их timeoutу.
python -m app.modules.cascading.cli full legacy_crypto_main \
    --amount 1500 --method sbp --option-code sber
```

Флаги:

* `--json` — машиночитаемый output (для cron / monitoring wrappers).
  Каждый шаг — это объект `ProbeResult(provider_code, action, ok,
  summary, details, error)`.
* `CASCADE_PROBE_HTTP_TRACE=1` — env-переменная, включает DEBUG-логи
  httpx + httpcore. Незаменимо когда провайдер возвращает голый
  401 без описания.

Что покрывает каждая subcommand:

| Subcommand | Что вызывает | Безопасно? | Когда использовать |
|---|---|---|---|
| `list`     | SELECT из БД | да | Узнать `code` провайдера |
| `ping`     | `get_balance` + `get_upstream_rate` (если есть) | да | Cron health-check, после ротации ключей |
| `balance`  | `get_balance` | да | Финансовый мониторинг |
| `issue`    | `issue_requisite` | **нет** — заблокирует трейдера | После доплоя нового адаптера, разовая ручная проверка |
| `cancel`   | `cancel_request` | да | Откатить ручной issue |
| `poll`     | `poll_status` | да | Дебаг конкретного заказа |
| `full`     | `ping + issue + cancel` | да *(всегда откатывает)* | CI-приёмка перед релизом адаптера, ежедневный smoke |

Запуск изнутри docker-compose стека на dev:

```bash
docker exec -e CASCADE_PROBE_HTTP_TRACE=1 dev_backend \
    python -m app.modules.cascading.cli full legacy_crypto_main \
    --amount 1500 --method sbp --option-code sber
```

Конкретно эту инвокацию можно повесить в cron / GitLab schedule:
прошло — провайдер живой, упало — алерт в Telegram.

### Юнит-тесты — копипаст шаблон ниже

```python
# tests/unit/test_provider_adapter_example.py
from unittest.mock import MagicMock
import pytest, json
from decimal import Decimal

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.core.security import encrypt_api_secret
from app.modules.cascading.integrations.base import ProviderRequisiteResponse
from app.modules.cascading.integrations.example import ExampleAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


def _provider(settings=None):
    p = MagicMock()
    p.id = 1
    p.adapter_type = "example"
    p.base_url = "https://api.example.com"
    p.api_secret_encrypted = encrypt_api_secret("secret")
    p.api_key_encrypted = None
    p.webhook_secret_encrypted = encrypt_api_secret("wh-secret")
    p.settings = settings or {}
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


@pytest.mark.asyncio
async def test_issue_requisite_happy_path():
    adapter = ExampleAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        # Можно asserts'ы прямо в responder — проверь headers / body / URL.
        assert req.headers["Authorization"].startswith("Bearer ")
        body = json.loads(req.read())
        assert body["amount"] == "1000.00"
        return _FakeResponse(200, {
            "data": {"id": "X-1", "bank": "Сбер", "account": "...",
                     "holder": "...", "amount": "1000.00", "rate": "95.0"},
        })

    patcher, captured = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.CARD,
                "currency": ...,
                "payment_option_code": None,
            },
            idempotency_key="abc",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "X-1"
    # Captured клиент знает все calls, шапки, body.
    assert len(captured) == 1
    assert captured[0].calls[-1].method == "POST"
```

Testkit живёт в [`tests/unit/cascade_adapter_testkit.py`](../../../../tests/unit/cascade_adapter_testkit.py).
Альтернативно — `patch_httpx` как context-manager:

```python
from tests.unit.cascade_adapter_testkit import patch_httpx

with patch_httpx(responder) as captured:
    ...
```

---

## Auto-discovery: как это работает

[`registry.py`](registry.py) при первом импорте проходит по
`app.modules.cascading.integrations.*` через `pkgutil.iter_modules`, ловит все
подклассы `ProviderAdapter` с непустым `code` и регистрирует их экземпляры.
Никаких правок не нужно.

Тесты могут регистрировать стаб-адаптеры руками через `registry.register(adapter)`
— см. как это сделано в `test_cascading_service.py` для `_ScriptedAdapter`.

---

## Курс провайдера vs наш курс

У каждого `CascadeProvider` есть `rate_source` ∈ `{provider, platform}`:

* `provider` — мы берём курс из ответа адаптера (`ProviderRequisiteResponse.provider_rate`).
  Применимо только если у адаптера `supports_provider_rate = True`.
  Когда `provider_rate=None` в ответе, базовый сервис пробует посчитать как
  `amount_fiat / amount_usdt`.

* `platform` — мы используем `RateConfig.current_rate` по `rate_config_id`,
  привязанному к провайдеру (тот же справочник курсов, что и у мерчанта).
  Подходит для адаптеров типа Mock, или когда нужно фиксировать курс на нашей
  стороне.

В UI на вкладке «Курс» провайдера админ переключает radio + (опционально)
выбирает RateConfig. Адаптеру делать ничего не нужно — это решает сервис.

---

## Балансы

`get_balance(provider)` — опциональный метод. Если адаптер его реализует,
admin UI на вкладке «Баланс» провайдера автоматически добавит блок
«Баланс у провайдера (live)» с кнопкой «Обновить». Эндпоинт
`GET /api/v1/cascade/providers/{id}/upstream-balance` уже маршрутизирует
запрос в адаптер.

Адаптеры из репо:

- `mock.py` — читает `settings.upstream_balance` (для отладки UI).
- `legacy_crypto.py` — `None` (у них нет публичного balance endpoint).
- `swifty.py` — `GET /v1/public/merchant/{merchant_id}/balance` → `data.balance`.
- `bridgepay.py` — `GET /api/merchant/accounts` → массив, фильтрует USDT.
- `bitzone.py` — `GET /payment/account` → `balance` (целое в smallest USDT-units, делим на 1e6).
- `garex.py` — `GET /api/merchant/balance` → `amount` (USDT). Дополнительно `get_upstream_rate` через `GET /api/merchant/rate`.
- `payscrow.py` — `GET /api/v1/finance/balance` → `deposit.available` (фиат, спендебельный баланс терминала).
- `bitwire.py` — `GET /api/merchant/balance` → `balance` (фиат, баланс стора).

---

## Что делает фронт автоматически

Никаких правок React/Vue при добавлении адаптера. Всё через
[`/api/v1/cascade/adapters`](../../../api/v1/endpoints/cascade.py) → `ProviderAdapter.describe()`:

- Появляется в дропдауне «Тип интеграции» (`CascadeProvidersView.vue`).
- Под дропдауном — `description`.
- На вкладке «Настройки адаптера» — форма генерится из `SETTINGS_SCHEMA`
  через `AdapterSettingsForm.vue`.
- На вкладке «Курс» — radio `Курс провайдера / Наш курс`. Если
  `supports_provider_rate = False` — первый вариант дисейблится.
- На вкладке «Баланс» — отображается upstream-баланс если `get_balance`
  возвращает не-None.

---

## Архитектура

```
┌──────────────────────────────────────────────────────────────────┐
│  CascadingService.try_cascade(merchant, order_data)              │
│   ├─ pick group (GROUPED) / score providers (POOLED)             │
│   ├─ race / sequential                                           │
│   └─ for each provider:                                          │
│       adapter = registry.get(provider.adapter_type)              │
│       └─ adapter.issue_requisite(...) ──► ProviderRequisiteResponse │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  ProviderAdapter (base.py)                                       │
│   • sign_request (knobs: AUTH_*, TIMESTAMP_*, SIGNATURE_*,       │
│     IDEMPOTENCY_*, SIGN_REQUESTS, EXTRA_AUTH_HEADER)             │
│   • acquire_token (async, default = get_token; override для      │
│                    JWT/OAuth с refresh)                          │
│   • verify_callback_signature (HMAC ИЛИ token-compare через      │
│                                WEBHOOK_TOKEN_HEADER)             │
│   • verify_and_decode_callback (один шаг для parse_callback)     │
│   • get_webhook_signing_secret (через WEBHOOK_SECRET_SOURCE)     │
│   • request_signed (raises) / safe_request (returns None)        │
│   • upload_file (single file) / upload_files (multi-file array)  │
│   • file_to_data_url (base64 inline для PATCH-style endpoints)   │
│   • resolve_method_value / resolve_payment_method_from           │
│     resolve_bank_code / validate_provider_config /               │
│     parse_envelope_or_refusal                                    │
│   • parse_provider_status / refusal_from_response                │
│   • get_token / get_api_key / get_webhook_secret / get_settings  │
│   • safe_decimal / parse_iso8601 / default_expires_at /          │
│     coerce_payment_method / format_amount / json_or_none /       │
│     decode_callback_body / canonical_json /                      │
│     serialize_request_body / compute_signature                   │
│                                                                  │
│  default: supports / issue_requisite (Template Method)           │
│  abstract: build_payin_request / parse_payin_response /          │
│            cancel_request / notify_receipt / raise_dispute /     │
│            parse_callback (опц. query_params для GET-callback'ов)│
│  optional: poll_status / get_balance                             │
└──────────────────────────────────────────────────────────────────┘
                              │
  ┌─────────┬─────┬───────────┴──────┬─────────┬──────────┬─────────┐
  ▼         ▼     ▼                  ▼         ▼          ▼         ▼
legacy_  swifty bridgepay        bitzone   garex     payscrow   bitwire
crypto   (X-    (X-Identity      (x-api-   (Bearer,  (X-API-Key (Bearer
(HMAC-    Sec-   + X-Sig SHA1     key,      no sig,   no sig,    <JWT с
SHA256    ret,   base64,          api_key   PATCH-    X-API-Key  refresh>
Bearer)   X-     full URL,        reuse     status    reuse for  + X-Api-
          Hash   token-compare    for       unifies   webhook,   Key,
          web-   webhook)         webhook,  cancel/   cancel /   GET-call-
          hook)                   upload?   receipt/  receipt =  back с
                                  query)    dispute)  no-ops,    query
                                                      files[]    string)
                                                      multi-file
                                                      dispute)
```

Каждая конкретная интеграция занимает один файл и переиспользует базу через
наследование. Никаких хитросплетений, никаких циклических зависимостей.

---

## Размер новой интеграции

В среднем по существующим:

- **~80–100 строк** — `SETTINGS_SCHEMA` декларация и class-атрибуты
- **~30 строк** — `issue_requisite` (build body + парсинг ответа через helpers)
- **~5 строк** — `cancel_request` (one `safe_request` call)
- **~10 строк** — `notify_receipt` (one `upload_file` call)
- **~15 строк** — `raise_dispute`
- **~15 строк** — `parse_callback`
- **~12 строк** — `poll_status` (опционально)
- **~12 строк** — `get_balance` (опционально)
- **~20–50 строк** — провайдер-специфичные helper'ы (маппинг банков, кодов)

**Итого 200–250 строк бизнес-логики + декларации.** Никакого `httpx`,
`hmac`, `Path`, ручных try/except — всё через helpers.

Тесты — 250–400 строк, шаблон в любом из трёх существующих `test_provider_adapter_*.py`.
