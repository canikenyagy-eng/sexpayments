<!--
  Interactive preview for a merchant's appeal-id mask (dispute_id_mask).

  Mirrors the backend grammar (app/modules/merchants/dispute_mask.py):
    "N" | "word:N" → the N-th whitespace token (1-based)
    "uuid:N"       → the N-th UUID-shaped token
  The admin pastes a sample appeal message; we highlight the exact token the
  mask would pick (by position) and show it. Backend additionally resolves the
  token against the DB — this preview only shows the positional pick.
-->
<template>
  <div class="rounded-xl border border-border bg-bg-card/40 p-3">
    <p class="mb-2 text-xs font-bold text-text-secondary">Проверка маски</p>

    <p v-if="!mask.trim()" class="text-xs text-text-muted">
      Укажите маску выше — здесь можно будет проверить, какой токен из текста она возьмёт.
    </p>

    <p v-else-if="!maskValid" class="text-xs text-status-danger">
      Неверный формат маски. Допустимо: «4» или «word:4» (4-е слово), «uuid:2» (2-й uuid).
    </p>

    <template v-else>
      <textarea
        v-model="sample"
        rows="3"
        placeholder="Вставьте пример текста апелляции от мерчанта…"
        class="w-full rounded-xl border border-border bg-bg-card px-3 py-2 text-sm text-text-main placeholder-text-muted outline-none transition focus:border-accent"
      />

      <div v-if="sample.trim()" class="mt-2 space-y-1.5">
        <div
          class="whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-bg-surface px-3 py-2 text-sm leading-relaxed text-text-secondary"
        >
          <template v-for="(seg, i) in segments" :key="i">
            <mark v-if="seg.hit" class="rounded bg-accent/25 px-0.5 font-bold text-accent">{{ seg.text }}</mark>
            <span v-else>{{ seg.text }}</span>
          </template>
        </div>

        <p class="text-xs">
          <span class="text-text-muted">Будет взято: </span>
          <code
            v-if="extracted"
            class="rounded bg-accent/15 px-1 py-0.5 font-mono font-bold text-accent"
          >{{ extracted }}</code>
          <span v-else class="font-bold text-status-warning">— ничего (позиция вне диапазона текста)</span>
        </p>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ mask: string }>()

const sample = ref('')

// Same grammar as the backend mask parser.
const MASK_RE = /^\s*(?:(word|uuid)\s*:\s*)?(\d{1,3})\s*$/i

function isUuid(t: string): boolean {
  // Accept hyphenated and bare-32-hex forms (Python's uuid.UUID is lenient).
  return /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i.test(t)
}

const maskParsed = computed(() => {
  const m = props.mask.match(MASK_RE)
  if (!m) return null
  const n = parseInt(m[2], 10)
  if (!Number.isFinite(n) || n < 1) return null
  return { mode: (m[1] || 'word').toLowerCase() as 'word' | 'uuid', n }
})

const maskValid = computed(() => maskParsed.value !== null)

interface Tok {
  text: string
  start: number
  end: number
}

// Tokens + their positions in the original text (mirrors Python str.split()).
const tokens = computed<Tok[]>(() => {
  const out: Tok[] = []
  const re = /\S+/g
  let m: RegExpExecArray | null
  while ((m = re.exec(sample.value)) !== null) {
    out.push({ text: m[0], start: m.index, end: m.index + m[0].length })
  }
  return out
})

const hitTok = computed<Tok | null>(() => {
  const p = maskParsed.value
  if (!p) return null
  const list = p.mode === 'uuid' ? tokens.value.filter((t) => isUuid(t.text)) : tokens.value
  return p.n <= list.length ? list[p.n - 1] : null
})

const extracted = computed(() => hitTok.value?.text ?? '')

const segments = computed<{ text: string; hit: boolean }[]>(() => {
  const hit = hitTok.value
  const text = sample.value
  if (!hit) return [{ text, hit: false }]
  return [
    { text: text.slice(0, hit.start), hit: false },
    { text: text.slice(hit.start, hit.end), hit: true },
    { text: text.slice(hit.end), hit: false },
  ].filter((s) => s.text.length > 0)
})
</script>
