<template>
  <!-- Bordered card with a small lowercase label. Building block of the order
       detail modals (admin / trader / merchant). The inner wrapper keeps the
       label out of OrderRow's :first-child border logic. -->
  <section class="rounded-xl border border-border bg-bg-card/40 p-3 pb-1">
    <div class="text-[11px] uppercase tracking-wider text-text-muted">{{ label }}</div>
    <div>
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{ label: string }>()
</script>
