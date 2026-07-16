// Canonicalise a Russian phone to +7XXXXXXXXXX regardless of how it was typed
// (spaces / dashes / parens / leading 8, 7 or +7 / a bare 10 digits). Mirrors the
// backend (app/common/phone.py). Unrecognisable input is returned unchanged.
export function normalizePhone(raw: string): string {
  if (!raw) return raw
  let digits = raw.replace(/\D/g, '')
  if (digits.length === 11 && (digits[0] === '7' || digits[0] === '8')) {
    digits = '7' + digits.slice(1)
  } else if (digits.length === 10) {
    digits = '7' + digits
  } else {
    return raw
  }
  return '+' + digits
}

// Requisite methods whose account_number is a phone number (vs a card number).
export function isPhoneMethod(method?: string | null): boolean {
  return method === 'sbp' || method === 'sim'
}
