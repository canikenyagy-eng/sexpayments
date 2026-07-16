<template>
  <div class="overflow-hidden rounded-2xl border border-border bg-panel-gradient" style="contain: layout style">
    <div class="overflow-x-auto">
      <table class="w-full text-sm" style="table-layout: auto">
        <thead>
          <tr class="border-b border-border bg-bg-surface/50">
            <th
              v-for="col in columns"
              :key="col.key"
              class="whitespace-nowrap px-4 py-3 text-left text-xs font-bold uppercase tracking-wider text-text-muted"
              :class="col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : ''"
              :style="col.width ? { width: col.width } : undefined"
            >
              <slot :name="`header-${col.key}`">{{ col.label }}</slot>
            </th>
            <th v-if="$slots.actions" class="px-4 py-3 text-right text-xs font-bold uppercase tracking-wider text-text-muted">
              Действия
            </th>
          </tr>
        </thead>
        <tbody v-if="!loading && rows.length > 0">
          <tr
            v-for="(row, idx) in rows"
            :key="rowKey ? row[rowKey] : idx"
            class="border-b border-border/50 transition hover:bg-bg-hover/50"
            :class="[
              { 'cursor-pointer': clickable },
              rowClass ? rowClass(row) : ''
            ]"
            @click="clickable ? $emit('row-click', row) : undefined"
          >
            <td
              v-for="col in columns"
              :key="col.key"
              class="whitespace-nowrap px-4 text-text-secondary"
              :class="[
                col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : '',
                col.cellClass || 'py-3'
              ]"
              :style="col.width ? { width: col.width } : undefined"
            >
              <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
                {{ row[col.key] ?? '—' }}
              </slot>
            </td>
            <td v-if="$slots.actions" class="whitespace-nowrap px-4 py-3 align-middle" @click.stop>
              <div class="flex items-center justify-end gap-2">
                <slot name="actions" :row="row" />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="loading" class="flex min-h-[200px] flex-col items-center justify-center gap-2">
      <img
        src="/logos/logo_anim.svg"
        alt="Загрузка"
        class="h-10 w-auto select-none"
        draggable="false"
      />
      <span class="text-sm text-text-muted">Загрузка...</span>
    </div>

    <div v-else-if="rows.length === 0" class="flex min-h-[200px] items-center justify-center text-center">
      <div>
        <p class="text-lg font-bold text-text-muted">Нет данных</p>
        <p class="mt-1 text-sm text-text-muted/70">{{ emptyText }}</p>
      </div>
    </div>

    <div
      v-if="!loading && rows.length > 0 && showPagination"
      class="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3"
    >
      <div class="flex items-center gap-3 text-xs text-text-muted">
        <span class="whitespace-nowrap">
          Страница <span class="font-bold text-text-main">{{ currentPage }}</span>
          из <span class="font-bold text-text-main">{{ Math.max(totalPages, 1) }}</span>
          <template v-if="totalItems != null">
            · всего <span class="font-bold text-text-main">{{ totalItems }}</span>
          </template>
        </span>
        <div class="flex items-center gap-2">
          <span class="whitespace-nowrap">На странице:</span>
          <select
            :value="perPage"
            class="rounded-lg border border-border bg-bg-surface px-2 py-1 text-xs font-semibold text-text-main focus:border-accent focus:outline-none"
            @change="onPerPageChange(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in perPageOptions" :key="opt" :value="opt">{{ opt }}</option>
          </select>
        </div>
      </div>
      <div class="flex items-center gap-1">
        <button
          type="button"
          :disabled="currentPage <= 1"
          class="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-bg-surface text-text-secondary transition hover:border-accent hover:text-text-main disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-border disabled:hover:text-text-secondary"
          title="Первая страница"
          @click="$emit('page-change', 1)"
        >
          <ChevronsLeft class="h-4 w-4" />
        </button>
        <button
          type="button"
          :disabled="currentPage <= 1"
          class="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-bg-surface text-text-secondary transition hover:border-accent hover:text-text-main disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-border disabled:hover:text-text-secondary"
          title="Предыдущая"
          @click="$emit('page-change', currentPage - 1)"
        >
          <ChevronLeft class="h-4 w-4" />
        </button>
        <button
          type="button"
          :disabled="currentPage >= totalPages"
          class="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-bg-surface text-text-secondary transition hover:border-accent hover:text-text-main disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-border disabled:hover:text-text-secondary"
          title="Следующая"
          @click="$emit('page-change', currentPage + 1)"
        >
          <ChevronRight class="h-4 w-4" />
        </button>
        <button
          type="button"
          :disabled="currentPage >= totalPages"
          class="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-bg-surface text-text-secondary transition hover:border-accent hover:text-text-main disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:border-border disabled:hover:text-text-secondary"
          title="Последняя страница"
          @click="$emit('page-change', totalPages)"
        >
          <ChevronsRight class="h-4 w-4" />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ChevronsLeft, ChevronLeft, ChevronRight, ChevronsRight } from 'lucide-vue-next'

export interface Column {
  key: string
  label: string
  align?: 'left' | 'center' | 'right'
  cellClass?: string
  /** Fixed column width (e.g. '1%' to shrink to content, or '48px'). */
  width?: string
}

withDefaults(defineProps<{
  columns: Column[]
  rows: Record<string, any>[]
  rowKey?: string
  loading?: boolean
  clickable?: boolean
  emptyText?: string
  currentPage?: number
  totalPages?: number
  totalItems?: number
  perPage?: number
  perPageOptions?: number[]
  showPagination?: boolean
  rowClass?: (row: Record<string, any>) => string | undefined
}>(), {
  loading: false,
  clickable: false,
  emptyText: 'Записи не найдены',
  currentPage: 1,
  totalPages: 1,
  perPage: 25,
  perPageOptions: () => [10, 25, 50, 100],
  showPagination: true,
})

const emit = defineEmits<{
  'row-click': [row: Record<string, any>]
  'page-change': [page: number]
  'per-page-change': [size: number]
}>()

function onPerPageChange(value: string) {
  const size = Number(value)
  if (!Number.isNaN(size)) emit('per-page-change', size)
}
</script>
